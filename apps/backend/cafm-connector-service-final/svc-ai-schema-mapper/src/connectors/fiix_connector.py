"""Fiix CMMS platform schema connector.

Fetches schema from the Fiix API and converts it to canonical field mapper.
Used for platform-native schema mapping in svc-ai-schema-mapper.

Key design:
- OBJECT_FIELDS contains verified field lists for all 88 Fiix objects.
  Schema extraction uses these static definitions so empty tables still
  contribute their full field list — no records required.
- Sample records are fetched on a best-effort basis for type inference.
"""

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    raise ImportError("Install requests: pip install requests")

from cafm_shared.logging import get_logger

from .fiix_plenum_mappings import (
    build_field_aliases_by_object,
    build_internal_vendor_aliases,
    resolve_plenum_column,
)

logger = get_logger(__name__)

# Where the extracted Fiix schema (mapper config) is persisted so we don't re-pull
# from the Fiix API (which has a daily quota) on every schema-mapping run.
_FIIX_SCHEMA_CACHE_DIR = Path(
    os.getenv("FIIX_SCHEMA_CACHE_DIR", str(Path(__file__).parent / "_fiix_schema_cache"))
)


def get_cached_fiix_mapper_config(connector, *, force_refresh: bool = False) -> Dict[str, Any]:
    """Return the Fiix mapper config (extracted tables + columns), served from a local
    cache keyed by Fiix subdomain so the Fiix API is hit ONCE and reused thereafter.

    Pass force_refresh=True (or set env FIIX_FORCE_REFRESH=1) to re-pull from Fiix and
    overwrite the cache. The cache file lives under FIIX_SCHEMA_CACHE_DIR.
    """
    if os.getenv("FIIX_FORCE_REFRESH", "").strip().lower() in ("1", "true", "yes"):
        force_refresh = True
    sub = getattr(getattr(connector, "api", None), "subdomain", "") or "default"
    key = hashlib.sha1(sub.encode("utf-8")).hexdigest()[:16]
    cache_file = _FIIX_SCHEMA_CACHE_DIR / f"mapper_config_{key}.json"

    if not force_refresh and cache_file.exists():
        try:
            cfg = json.loads(cache_file.read_text(encoding="utf-8"))
            connector.logger.info(
                f"[FiixConnector] Using CACHED Fiix schema for '{sub}' "
                f"({cache_file.name}) — NO Fiix API call made"
            )
            return cfg
        except Exception as e:  # noqa: BLE001 — corrupt cache → re-fetch
            connector.logger.warning(f"[FiixConnector] Schema cache read failed ({e}); re-fetching")

    cfg = connector.get_mapper_config()  # hits the Fiix API
    try:
        _FIIX_SCHEMA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cfg), encoding="utf-8")
        connector.logger.info(
            f"[FiixConnector] Stored Fiix schema for '{sub}' → {cache_file} "
            f"(reused on next run; set FIIX_FORCE_REFRESH=1 to re-pull)"
        )
    except Exception as e:  # noqa: BLE001 — caching is best-effort
        connector.logger.warning(f"[FiixConnector] Could not write schema cache ({e})")
    return cfg


class FiixError(Exception):
    """Fiix API error."""
    def __init__(self, message="", code=None, leg=None, stack_trace=None):
        self.code = code
        self.leg = leg
        self.stack_trace = stack_trace
        super().__init__(f"[{code or 'ERR'}] {message}")


class FiixAPI:
    """Minimal Fiix CMMS API connector for schema extraction."""

    API_VERSION = "2.3.1"

    def __init__(self, subdomain: str, app_key: str, access_key: str,
                 secret_key: str, timeout: int = 30):
        self.subdomain = subdomain
        self.app_key = app_key
        self.access_key = access_key
        self.secret_key = secret_key
        self.timeout = timeout
        self.base_url = f"https://{subdomain}.macmms.com/api/"
        self.client_version = {"major": 2, "minor": 48, "patch": 1}
        self._request_count = 0

        self._session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"]
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retry))

    def _build_url(self, base_url=None, extra_params=None):
        params = {
            "service": "cmms",
            "timestamp": str(int(time.time() * 1000)),
            "appKey": self.app_key,
            "accessKey": self.access_key,
            "signatureMethod": "HmacSHA256",
            "signatureVersion": "1",
        }
        if extra_params:
            params.update(extra_params)
        return f"{base_url or self.base_url}?{urllib.parse.urlencode(params)}"

    def _sign(self, url):
        message = url
        for prefix in ("https://", "http://"):
            if message.startswith(prefix):
                message = message[len(prefix):]
                break
        return hmac.HMAC(
            key=self.secret_key.encode("utf-8"),
            msg=message.encode("utf-8"),
            digestmod=hashlib.sha256
        ).hexdigest().lower()

    def _post(self, payload, base_url=None, extra_params=None):
        url = self._build_url(base_url, extra_params)
        signature = self._sign(url)
        headers = {
            "Content-Type": "text/plain",
            "Accept": "application/json",
            "Authorization": signature,
        }
        body = json.dumps(payload, separators=(",", ":"))

        try:
            resp = self._session.post(
                url, data=body.encode("utf-8"),
                headers=headers, timeout=self.timeout
            )
        except requests.exceptions.ConnectionError as e:
            raise FiixError(f"Connection failed: {e}")
        except requests.exceptions.Timeout:
            raise FiixError("Request timed out")

        self._request_count += 1

        if resp.status_code != 200:
            raise FiixError(f"HTTP {resp.status_code}: {resp.text[:300]}")

        try:
            result = resp.json()
        except json.JSONDecodeError:
            raise FiixError(f"Invalid JSON response: {resp.text[:200]}")

        if result.get("error"):
            err = result["error"]
            raise FiixError(err.get("message", "Unknown"), err.get("code"),
                            err.get("leg"), err.get("stackTrace"))
        return result

    def _base_payload(self):
        return {
            "clientVersion": self.client_version,
            "requestSentUnixTime": int(time.time() * 1000),
        }

    def test_connection(self) -> bool:
        try:
            payload = self._base_payload()
            payload.update({"_maCn": "RpcRequest", "name": "Ping", "action": "Ping"})
            result = self._post(payload)
            return "error" not in result or not result.get("error")
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return False

    def find(self, class_name, fields="id", filters=None,
             order_by=None, start_index=0, max_objects=100):
        payload = self._base_payload()
        payload.update({
            "_maCn": "FindRequest",
            "className": class_name,
            "fields": fields,
            "startIndex": start_index,
            "maxObjects": min(max_objects, 1000),
        })
        if filters:
            payload["filters"] = filters
        if order_by:
            payload["orderBy"] = order_by
        return self._post(payload)

    @property
    def request_count(self):
        return self._request_count


class FiixSchemaConnector:
    """Fiix platform schema connector for mapper generation."""

    # ── Authoritative list of all 88 Fiix object class names ─────────────
    # Verified against plenumtechnology.macmms.com (API v2.3.1-alpha).
    ALL_OBJECTS: List[str] = [
        "Account", "Asset", "AssetBusiness", "AssetCategory",
        "AssetClassification", "AssetClassificationLog", "AssetConsumingReference",
        "AssetEvent", "AssetEventType", "AssetOfflineTracker", "AssetUser",
        "BOMGroup", "BOMGroupPart", "BillingTerm", "Business",
        "BusinessClassification", "BusinessGroup", "BusinessRole",
        "CalendarEvent", "ChargeDepartment", "Country", "Currency",
        "CycleCount", "File", "FileContents", "InventoryTransaction",
        "MaintenanceType", "MeterReading", "MeterReadingUnit",
        "MiscCost", "MiscCostType", "Move", "MoveAsset", "MoveBack",
        "MoveBackAsset", "MoveSiteManager", "MoveStatus", "Priority",
        "Project", "PurchaseOrder", "PurchaseOrderAdditionalCost",
        "PurchaseOrderAdditionalCostType", "PurchaseOrderLineItem",
        "PurchaseOrderLog", "PurchaseOrderStatus", "RCAAction", "RCACause",
        "RCAGrouping", "RCAGroupingAction", "RCAGroupingCause", "RCAProblem",
        "RFQ", "RFQLineItem", "RFQStatus", "ReasonToSetAssetOffline",
        "ReasonToSetAssetOnline", "Receipt", "ReceiptLineItem", "ReceiptStatus",
        "RegionUser", "RegionUserGroup", "ReportsToResolved", "ScheduleTrigger",
        "ScheduledMaintenance", "ScheduledMaintenanceAsset",
        "ScheduledMaintenanceNesting", "ScheduledMaintenancePart",
        "ScheduledMaintenanceUser", "ScheduledTask", "SiteUser", "SiteUserGroup",
        "Stock", "StockCycleCount", "StockHistory", "StockTxType",
        "TaskGroup", "User", "UserCertification", "Warranty",
        "WorkOrder", "WorkOrderAsset", "WorkOrderBusiness", "WorkOrderPart",
        "WorkOrderStatus", "WorkOrderStatusTransition", "WorkOrderTask",
        "WorkOrderTaskFile", "WorkOrderUser",
    ]

    # ── Verified field lists per object (from live API testing) ──────────
    # Comma-separated strings matching exact Fiix field names.
    # These are used as the authoritative schema even for empty tables.
    OBJECT_FIELDS: Dict[str, str] = {
        "Account":
            "id, strCode, strDescription, intUpdated",
        "Asset":
            "id, strName, strCode, strDescription, strMake, strModel, strSerialNumber, strBarcode, strInventoryCode, strAddress, strCity, strProvince, strPostalCode, strNotes, strBinNumber, strRow, strAisle, strStockLocation, strShippingTerms, strCriticality, strUnspcCode, strQuotingTerms, strRFQTriggerSiteLevelSetting, strMASourceProduct, strCustomerIds, strVendorIds, qtyMinStockCount, qtyStockCount, dblLatitude, dblLongitude, dblLastPrice, intSiteID, intAssetLocationID, intAssetParentID, intCategoryID, intSuperCategorySysCode, intAccountID, intChargeDepartmentID, intCountryID, intKind, bolIsSite, bolIsOnline, bolIsRegion, bolIsBillToFacility, bolIsShippingOrReceivingFacility, dtmCreated, intUpdated",
        "AssetBusiness":
            "id, intBusinessID, intBusinessRoleTypeID, intAssetID, intBusinessGroupID, bolSendRFQs, bolPreferredVendor, qtyEconomicBatchQuantity, strBusinessAssetNumber",
        "AssetCategory":
            "id, intParentID, strName, intSysCode, bolOverrideRules, intUpdated",
        "AssetClassification":
            "id, intAssetID, intSiteID, qtyAnnualUsage, intClassificationID",
        "AssetClassificationLog":
            "id, intAssetID, intSiteID, dtmDateApplied, intClassificationID",
        "AssetConsumingReference":
            "id, intConsumesAssetID, intBOMControlID, intAssetID, intBOMPartControlID, qtyMaxConsumption, intUpdated",
        "AssetEvent":
            "id, dtmDateSubmitted, intAssetEventTypeID, intAssetID, intSubmittedByUserID, intWorkOrderID, strAdditionalDescription",
        "AssetEventType":
            "id, strEventCode, strEventDescription, strEventName",
        "AssetOfflineTracker":
            "id, intAssetID, intWorkOrderID, intReasonOfflineID, intReasonOnlineID, intSetOfflineByUserID, intSetOnlineByUserID, dtmOfflineFrom, dtmOffLineTo, dblProductionHoursAffected, strOfflineAdditionalInfo, strOnlineAdditionalInfo, intUpdated",
        "AssetUser":
            "id, intAssetUserTypeID, dtmDateAdded, intUserID, intAssetID",
        "BOMGroup":
            "id, strName, dtmLastUpdated, intCreatedByUserID, intLastUpdatedByUserID",
        "BOMGroupPart":
            "id, intAssetID, intBOMGroupID, qtyMaxConsumption",
        "BillingTerm":
            "id, strName, intUpdated",
        "Business":
            "id, strName, strCode, strAddress, strCity, strProvince, strPostalCode, strTimezone, strPhone, strPhone2, strFax, strPrimaryEmail, strSecondaryEmail, strPrimaryContact, strNotes, intCountryID, intPrimaryCurrencyID, intUpdated",
        "BusinessClassification":
            "id, strName",
        "BusinessGroup":
            "id, strName, bolIsDefaultManufacturer, bolIsDefaultSupplier, intRelationshipType",
        "BusinessRole":
            "id, intBusinessID, intBusinessGroupID",
        "CalendarEvent":
            "id, intScheduledMaintenanceID, intScheduleTriggerID, dtmDate",
        "ChargeDepartment":
            "id, strCode, strDescription, intFacilityID, intUpdated",
        "Country":
            "id, strMid, strName, strShort2, strShort",
        "Currency":
            "id, strDescription, strISOCode, strName, strSymbol",
        "CycleCount":
            "id, bolComplete, bolIncludeClassA, bolIncludeClassB, bolIncludeClassC, bolIncludeNotClassified, dblGrossVariance, dblNetVariance, dblTotalValueCounted, dblTotalValueExpected, dtmCompleted, dtmCreated, intCompletedBy, intCreatedBy, intSiteID, strAisle, strRow, strBin, intUpdated",
        "File":
            "id, intWorkOrderID, intFileTypeID, strName, intSize, strNotes, intFileContentsID, intAssetID, intPurchaseOrderID, intScheduledMaintenanceID, strLink, intUpdated",
        "FileContents":
            "id, strName, strMimeType, intSize, intSysCode",
        "InventoryTransaction":
            "id, dblCostPerUnit, dblTotalCost, dtmDate, intInventoryChargeID, intInventoryChargedForID, intInventoryChargedFromID, intStockTxTypeID, qtyTxQuantity",
        "MaintenanceType":
            "id, strName, intSysCode, strColor, intUpdated",
        "MeterReading":
            "id, intAssetID, intWorkOrderID, intSubmittedByUserID, intMeterReadingUnitsID, dblMeterReading, dtmDateSubmitted, intUpdated",
        "MeterReadingUnit":
            "id, strName, strSymbol, intPrecision, intUpdated",
        "MiscCost":
            "id, intWorkOrderID, intMiscCostTypeID, strDescription, dblActualTotalCost, dblActualUnitCost, dblEstimatedTotalCost, dblEstimatedUnitCost, qtyEstQuantity, qtyQuantity, intUpdated",
        "MiscCostType":
            "id, strName, intUpdated",
        "Move":
            "id, intDestinationTypeID, intAssetDestinationID, intUserDestinationID, intBusinessDestinationID, intWorkOrderDestinationID, intProjectDestinationID, intSiteID, intFromSiteID, intMoveStatusID, intRequestedByID, intMovedByID, intConfirmedByID, intRejectedByID, strAisle, strRow, strBin, strNotes, dtmDateRequested, dtmMoveDate, dtmDateConfirmed, dtmDateRejected",
        "MoveAsset":
            "id, intAssetID, intMoveID, intSiteID, intMovedFromID, intReasonOfflineID, intReasonOnlineID, bolAway, bolPending, bolSetOffline, bolSetOnline, bolExclude, dtmReturnDate, dtmDateReturned, strFromAisle, strFromRow, strFromBin, strNotes",
        "MoveBack":
            "id, intMovedBackByUserID, intRequestedByID, intConfirmedByID, intRejectedByID, intMoveStatusID, intFromSiteID, intSiteID, dtmMoveBackDate, dtmDateRequested, dtmDateConfirmed, dtmDateCanceled, strNotes",
        "MoveBackAsset":
            "id, intAssetID, intMoveBackID, intOriginalMoveAssetID, intReasonOnlineID, intReasonOfflineID, intSiteID, bolPending, bolSetBackOnline, bolSetBackOffline, bolExclude, strToAisle, strToBin, strToRow, strNotes",
        "MoveSiteManager":
            "id, intSiteID, intUserID",
        "MoveStatus":
            "id, intSysCode, strDefaultLabel, strName",
        "Priority":
            "id, strName, intOrder, intSysCode, intUpdated",
        "Project":
            "id, intSiteID, strName, strDescription, dtmProjectedStartDate, dtmProjectedEndDate, dtmActualStartDate, dtmActualEndDate",
        "PurchaseOrder":
            "id, intCode, intSupplierID, intPurchaseOrderStatusID, intSiteID, intBillingTermID, intChargeDepartmentID, intCreatedByUserID, intLastUpdatedUserID, intAccountID, intAssetID, intBillToID, intBillToCountryID, intShipToID, intShipToCountryID, intSupplierCountryID, intPurchaseCurrencyID, intWorkOrderID, intLocationID, intSendToSupplierMethod, dblSubtotal, dblTotal, dblFreight, dblTax1, dblTax2, strPurchaseOrderReference, strTransactionID, strBillToAddress, strBillToCity, strBillToPostalCode, strBillToProvince, strShipToAddress, strShipToCity, strShipToPostalCode, strShipToProvince, strSupplierAddress, strSupplierCity, strSupplierPostalCode, strSupplierProvince, dtmDateCreated, dtmDateSubmitted, dtmDateExpectedDelivery, dtmDateReceived, dtmDateRequiredBy, dtmDateLastUpdated, intUpdated",
        "PurchaseOrderAdditionalCost":
            "id, intPurchaseOrderID, intPurchaseOrderAdditionalCostTypeID, intShippingTypeID, intBusinessID, strDescription, bolOverridePoLineItemTax, dblTaxRate, dblPrice",
        "PurchaseOrderAdditionalCostType":
            "id, intControlID, intSysCode, strName, bolAlwaysShowOnNewPo",
        "PurchaseOrderLineItem":
            "id, intPurchaseOrderID, intStockID, intAssetID, intAccountID, intChargeDepartmentID, intRequestedByUserID, intShipToLocationID, intSiteID, intSourceAssetID, intSourceWorkOrderID, intStockHistoryID, intSupplierID, intParentPurchaseOrderLineItemID, strDescription, qtyOnOrder, qtyRecieved, dblUnitPrice, dblTaxRate, dblRemoteOrgUnitPrice, bolAddedDirectlyToPurchaseOrder, bolProductionEquipmentDownWhileOnOrder, dtmDateCreated, dtmRequiredByDate, intUpdated",
        "PurchaseOrderLog":
            "id, intPurchaseOrderID, intFromStatusId, intToStatusId, intUserID, dtmDateLogged",
        "PurchaseOrderStatus":
            "id, intSysCode, intControlID, strDefaultLabel, strName, intUpdated",
        "RCAAction":
            "id, strCode, strDescription, bolActive, intUpdated",
        "RCACause":
            "id, strCode, strDescription, bolActive, intUpdated",
        "RCAGrouping":
            "id, intAssetCategoryID, intAssetID, intRCAProblemID, intUpdated",
        "RCAGroupingAction":
            "id, intRCAGroupingID, intRCAActionID, intUpdated",
        "RCAGroupingCause":
            "id, intRCACauseID, intRCAGroupingID, intUpdated",
        "RCAProblem":
            "id, strCode, strDescription, bolActive, intUpdated",
        "RFQ":
            "id, intSiteID, intCode, intRFQStatusID, intCreatedByUserID, intBusinessID, intSupplierID, intSupplierCountryID, intBillToID, intBillToCountryID, intShipToID, intShipToCountryID, strQuoteReferenceNumber, strMessageContent, strMessageSubject, strSupplierAddress, strSupplierCity, strSupplierPostalCode, strSupplierProvince, strBillToAddress, strBillToCity, strBillToPostalCode, strBillToProvince, strShipToAddress, strShipToCity, strShipToPostalCode, strShipToProvince, dtmDateRequiredResponse, dtmDateExpectedDelivery, dtmDateSent",
        "RFQLineItem":
            "id, intRFQID, intParentRFQLineItemID, intPurchaseOrderLineItemID, intAssetID, strDescription, strBusinessAssetNumber, qtyRequested, qtyQuoted, dblQuotedPricePerUnit, dblQuotedPriceTotal",
        "RFQStatus":
            "id, strName, strDefaultLabel, intSysCode, intControlID",
        "ReasonToSetAssetOffline":
            "id, strName, intUpdated",
        "ReasonToSetAssetOnline":
            "id, strName, intUpdated",
        "Receipt":
            "id, intCode, intPurchaseOrderID, intReceiptStatusID, intSiteID, intSupplierID, intPurchaseCurrencyID, strPackingSlip, dtmDateReceived, dtmDateOrdered, intUpdated",
        "ReceiptLineItem":
            "id, intReceiptID, intPurchaseOrderLineItemID, intStockID, intAssetID, intReceiveToStockID, intReceiveToFacilityID, intParentReceiptLineItemID, qtyQuantityReceived, qtyQuantityOrdered, dblPurchasePricePerUnit, strDescription, dtmDateExpiryOfInventoryItems, intUpdated",
        "ReceiptStatus":
            "id, strName, intControlID, intSysCode, strDefaultLabel, intUpdated",
        "RegionUser":
            "id, intRegionID, intUserID",
        "RegionUserGroup":
            "id, intGroupID, intRegionUserID",
        "ReportsToResolved":
            "id, intChildID, intParentID",
        "ScheduleTrigger":
            "id, strType, strTSType, strRType, strROType, strRRType, strTRType, strMrLogic, strScheduleDescription, strDatLogicHourly, strDatLogicDaily, strDatLogicMonthly, strDatLogicYearly, intScheduledMaintenanceID, intAssetID, intAssetEventTypeID, intRMeterReadingUnitID, intROMeterReadingUnitID, intRREndAfter, intTREndAfter, intTSDEveryDays, intTSHEveryHours, intTSMDayOfMonth, intTSMEveryMonths, intTSWEveryWeeks, intTSYDayOfMonth, intTSYEveryYears, intTSYMonthOfYear, intTRTriggerTime, dblLastMeterReading, dblRMeterReading, dblROMeterReading, dblRREndBy, dblRRStart, dtmLastTriggered, datTRStart, datTREndBy, bolTSWMonday, bolTSWTuesday, bolTSWWednesday, bolTSWThursday, bolTSWFriday, bolTSWSaturday, bolTSWSunday, bolMrByWOClosed, bolCreateWorkOrderOnStartDate, intUpdated",
        "ScheduledMaintenance":
            "id, strCode, strDescription, strCompletionNotes, intSiteID, intPriorityID, intMaintenanceTypeID, intScheduledMaintenanceStatusID, intStartAsWorkOrderStatusID, intProjectID, intAccountID, intChargeDepartmentID, intRequestorUserID, intSuggestedCompletion, bolCanFireSMWithOpenWO, bolWORequiresSignature, dtmCreateDate, dtmUpdatedDate, intUpdated",
        "ScheduledMaintenanceAsset":
            "id, intScheduledMaintenanceID, intAssetID, intUpdated",
        "ScheduledMaintenanceNesting":
            "id, intCurrentIterationCycle, intMultiplier, intNameIdentifier, intParentId, intScheduledMaintenanceID, strDescription",
        "ScheduledMaintenancePart":
            "id, intScheduledMaintenanceID, intPartID, intAssetID, intStockID, qtySuggestedQuantity",
        "ScheduledMaintenanceUser":
            "id, intScheduledMaintenanceID, intUserID, bolNotifyOnAssignment, bolNotifyOnCompletion, bolNotifyOnOnlineOffline, bolNotifyOnStatusChange, bolNotifyOnTaskCompleted",
        "ScheduledTask":
            "id, intTaskType, intAssetID, intAssignedToUserID, intMeterReadingUnitID, intOrder, intParentScheduledTaskID, intScheduledMaintenanceID, intScheduledMaintenanceNestingID, strDescription, dblTimeEstimatedHours, intUpdated",
        "SiteUser":
            "id, intSiteID, intUserID, intUpdated",
        "SiteUserGroup":
            "id, intSiteUserID, intGroupID, intUpdated",
        "Stock":
            "id, intAssetID, intFacilityID, qtyOnHand, qtyMinQty, qtyMaxQty, strAisle, strRow, strBin, bolDeactivated, intUpdated",
        "StockCycleCount":
            "id, intCycleCountID, intStockID, intCountedBy, qtyExpected, qtyStockCount, dblPrice, dtmDateCounted, intUpdated",
        "StockHistory":
            "id, intStockID, intStockTxID, intStockTxTypeID, intInventoryMethodType, intUserID, strDescription, qtyAfter, qtyBefore, qtyMethodQty, qtyQuantity, dblLastPrice, dtmDate, intUpdated",
        "StockTxType":
            "id, strName",
        "TaskGroup":
            "id, strName, intCreatedByUserID, dtmLastUpdated, intUpdated",
        "User":
            "id, strFullName, strUserName, strUserTitle, strEmailAddress, strPersonnelCode, strTelephone, strTelephone2, strAddress1, strAddress2, strCity, strState, strPostalCode, strNotes, strRequestNotes, strDefaultLoginLocation, intCountryID, intUserStatusID, bolGroup, bolApiManaged, dblHourlyRate, intUpdated",
        "UserCertification":
            "id, intUserID, intCertificationID, intFileContentsID, strName, strDescription, datValidFrom, datValidTo",
        "Warranty":
            "id, intAssetID, intProvider, intMeterReadingUnitsID, intWarrantyTypeID, intWarrantyUsageTermTypeID, strCertificateNumber, strDescription, strMeterReadingValueLimit, dtmDateAdded, dtmExpiryDate",
        "WorkOrder":
            "id, strCode, strDescription, strCompletionNotes, strAdminNotes, intWorkOrderStatusID, intPriorityID, intMaintenanceTypeID, intSiteID, intProjectID, intCompletedByUserID, intRequestedByUserID, intAccountID, intChargeDepartmentID, intScheduledMaintenanceID, intLastModifiedByUserID, intOriginWorkOrderTaskID, intRCAActionID, intRCACauseID, intRCAProblemID, strAssetIds, strAssets, strAssignedUserIds, strAssignedUsers, strCompletedByUserIds, strCompletedByUsers, strCustomerIds, strVendorIds, strEmailUserGuest, strNameUserGuest, strPhoneUserGuest, dtmDateCreated, dtmDateCompleted, dtmDateLastModified, dtmSuggestedStartDate, dtmSuggestedCompletionDate, intUpdated",
        "WorkOrderAsset":
            "id, intWorkOrderID, intAssetID, intUpdated",
        "WorkOrderBusiness":
            "id, intWorkOrderID, intAssetID, intBusinessID, intBusinessGroupID, intUpdated",
        "WorkOrderPart":
            "id, intWorkOrderID, intAssetID, intStockID, intPartID, qtySuggestedQuantity, qtyActualQuantityUsed, intUpdated",
        "WorkOrderStatus":
            "id, strName, intControlID, intSysCode, intUpdated",
        "WorkOrderStatusTransition":
            "id, intWorkOrderID, intFromWorkOrderStatusID, intToWorkOrderStatusID, intUserID, dtmDate",
        "WorkOrderTask":
            "id, intWorkOrderID, intAssetID, intTaskType, intOrder, strResult, strDescription, strTaskNotesCompletion, intAssignedToUserID, intCompletedByUserID, intMeterReadingUnitID, intParentWorkOrderTaskID, intTaskGroupControlID, dblTimeEstimatedHours, dblTimeSpentHours, dtmStartDate, dtmDateCompleted, intUpdated",
        "WorkOrderTaskFile":
            "id, intFileID, intWorkOrderTaskID",
        "WorkOrderUser":
            "id, intWorkOrderID, intUserID, bolNotifyOnAssignment, bolNotifyOnCompletion, bolNotifyOnOnlineOffline, bolNotifyOnStatusChange, bolNotifyOnTaskCompleted",
    }

    # Legacy flat map — prefer resolve_plenum_column(object, field) for context-aware names.
    # Built from GLOBAL + FK maps (ambiguous fields like strCode require object context).
    @classmethod
    def _legacy_field_mappings(cls) -> Dict[str, str]:
        from .fiix_plenum_mappings import FK_FIIX_TO_PLENUM, GLOBAL_FIIX_TO_PLENUM

        out = dict(GLOBAL_FIIX_TO_PLENUM)
        out.update(FK_FIIX_TO_PLENUM)
        return out

    FIELD_MAPPINGS: Dict[str, str] = {}  # populated after class body; see end of class

    # Keep CORE_OBJECTS as an alias for backward compatibility
    CORE_OBJECTS = ALL_OBJECTS

    # Additional Fiix classes referenced by the public API docs but NOT in the core
    # 88 quick-reference list. A tenant may expose more classes than the docs show
    # (custom objects, lookup tables, etc.). These are PROBED at connect time —
    # any the tenant doesn't support fail the probe and are silently dropped, so an
    # unknown/wrong name here can never break extraction. Extend this list (or let
    # discovery validate names supplied elsewhere) to capture a tenant's full set.
    EXTRA_OBJECT_CANDIDATES: List[str] = [
        "Location", "BusinessRoleType", "AssetUserType", "ShippingType", "FileType",
        "Certification", "WorkOrderStatusGroup", "BOMControl", "BOMGroupControl",
        "AssetType", "AssetSubType", "AssetMeter", "CustomField", "AssetCustomField",
        "WorkOrderCustomField", "Comment", "Notification", "WorkRequest", "Group",
        "UserGroup", "Region", "Inspection", "InspectionResult", "StockPurchaseItem",
        "TaskGroupTask", "TaskGroupAssetCategory", "Labour", "WorkOrderLabour",
        "ScheduledMaintenanceTrigger", "MaintenanceRequest", "Tenant", "Part",
    ]

    def __init__(self, subdomain: str, app_key: str, access_key: str,
                 secret_key: str, timeout: int = 30):
        self.api = FiixAPI(subdomain, app_key, access_key, secret_key, timeout)
        self.logger = get_logger(__name__)
        # Effective object list used by extraction. Starts as the verified 88 and is
        # expanded by discover_objects() to whatever the tenant actually supports.
        self._objects: List[str] = list(self.ALL_OBJECTS)

    def discover_objects(self) -> List[str]:
        """Probe the Fiix API for the classes this tenant actually supports.

        Tries the verified 88 + EXTRA_OBJECT_CANDIDATES with a cheap one-row find.
        Classes the API rejects (unknown class for this tenant) are dropped.
        Fully fail-safe: returns the verified 88 baseline on ANY error, so this can
        only ADD tables, never remove the working baseline.
        """
        try:
            seen: set[str] = set()
            candidates: List[str] = []
            for cls in [*self.ALL_OBJECTS, *self.EXTRA_OBJECT_CANDIDATES]:
                if cls not in seen:
                    seen.add(cls)
                    candidates.append(cls)

            supported: List[str] = []
            for cls in candidates:
                try:
                    self.api.find(cls, fields="id", max_objects=1)
                    supported.append(cls)
                except FiixError:
                    continue  # class not supported by this tenant — skip
                except Exception:
                    continue
            # Never drop a verified baseline object just because one probe hiccuped.
            merged = list(dict.fromkeys([*supported, *self.ALL_OBJECTS]))
            self._objects = merged
            extra = [o for o in merged if o not in self.ALL_OBJECTS]
            self.logger.info(
                f"[FiixConnector] Object discovery: {len(merged)} classes "
                f"({len(self.ALL_OBJECTS)} verified + {len(extra)} discovered: {extra})"
            )
            return merged
        except Exception as e:  # pragma: no cover - discovery is best-effort
            self.logger.warning(f"[FiixConnector] Object discovery failed ({e}); using verified 88")
            self._objects = list(self.ALL_OBJECTS)
            return self._objects

    def _parse_field_names(self, fields_str: str) -> List[str]:
        """Parse a comma-separated fields string into a list of field names.

        ``id`` is kept (displayed as a column + mapped id → id identity); it is the
        Fiix PK, converted to a deterministic UUID and written as the target ``id`` PK.
        """
        return [f.strip() for f in fields_str.split(",") if f.strip()]

    def extract_schema(self) -> Dict[str, Any]:
        """
        Extract Fiix schema for all 88 objects.

        Uses OBJECT_FIELDS as the authoritative field list — so every object
        contributes its full schema even if the table is empty.
        Sample records are fetched on a best-effort basis for type inference
        and sample values; failures are silently ignored.

        Returns dict keyed by ``{Object}.{field}`` with Fiix native field names preserved
        and optional ``plenum_target`` for migration mapping.
        """
        # Object discovery probes ~120 classes (88 verified + candidates) with one
        # find() EACH — ~120 Fiix API calls against a daily quota. The tenant is the
        # verified 88, so probing is OFF by default; set FIIX_DISCOVER_OBJECTS=1 to
        # re-enable it for tenants with extra classes.
        if os.getenv("FIIX_DISCOVER_OBJECTS", "").strip().lower() in ("1", "true", "yes"):
            objects = self.discover_objects()
        else:
            objects = list(self.ALL_OBJECTS)
            self._objects = objects

        self.logger.info(
            f"[FiixConnector] Extracting schema for {len(objects)} objects "
            f"(static field definitions + live sample values)..."
        )
        canonical_fields: Dict[str, Any] = {}

        for obj_name in objects:
            # Discovered-only classes (not in OBJECT_FIELDS) default to id; their
            # remaining columns are filled from any sampled record below.
            known_fields_str = self.OBJECT_FIELDS.get(obj_name, "id")
            field_names = self._parse_field_names(known_fields_str)
            # Id-only Fiix objects still count as source tables.
            if not field_names:
                field_names = ["id"]

            # Try to fetch sample records for value inference (best-effort)
            sample_values: Dict[str, Any] = {}
            records_sampled = 0
            try:
                result = self.api.find(obj_name, fields=known_fields_str, max_objects=10)
                records = result.get("objects", [])
                records_sampled = len(records)
                for record in records:
                    for field_name, value in record.items():
                        if field_name not in sample_values and value is not None:
                            sample_values[field_name] = value
            except FiixError as e:
                self.logger.debug(f"[FiixConnector] Sample fetch failed for {obj_name}: {e}")
            except Exception as e:
                self.logger.debug(f"[FiixConnector] Unexpected error sampling {obj_name}: {e}")

            # Register all known fields regardless of whether records existed
            added = 0
            for field_name in field_names:
                plenum_target = resolve_plenum_column(obj_name, field_name)
                canonical_fields[f"{obj_name}.{field_name}"] = {
                    "canonical": field_name,
                    "plenum_target": plenum_target,
                    "source_object": obj_name,
                    "field_name": field_name,
                    "sample_value": sample_values.get(field_name),
                    "records_sampled": records_sampled,
                }
                added += 1

            status = f"{records_sampled} sample records" if records_sampled else "no records (schema from static definition)"
            self.logger.info(f"[FiixConnector]   {obj_name}: {added} fields — {status}")

        self.logger.info(
            f"[FiixConnector] ✓ Schema extraction complete: "
            f"{len(canonical_fields)} total fields across {len(objects)} objects"
        )
        return canonical_fields

    def get_mapper_config(self) -> Dict[str, Any]:
        """
        Build a mapper configuration from Fiix schema.
        Returns JSON mapper format compatible with svc-ai-schema-mapper.
        """
        self.logger.info("[FiixConnector] Building mapper configuration...")

        if not self.api.test_connection():
            raise FiixError("Cannot connect to Fiix API. Check credentials.")

        fiix_schema = self.extract_schema()

        # tables_by_object: Fiix object → {fiix_field: plenum_column | null}
        # Source column names are always native Fiix; values are internal plenum targets.
        tables_by_object: Dict[str, Dict[str, Optional[str]]] = {}
        for key, info in fiix_schema.items():
            obj = info["source_object"]
            field_name = info["field_name"]
            plenum_target = info.get("plenum_target")
            tables_by_object.setdefault(obj, {})[field_name] = plenum_target

        # canonical_fields: plenum column → description (unique internal targets only)
        canonical_fields: Dict[str, str] = {}
        for obj, fields in tables_by_object.items():
            for fiix_field, plenum_col in fields.items():
                if plenum_col and plenum_col not in canonical_fields:
                    canonical_fields[plenum_col] = (
                        f"plenum_cafm column mapped from Fiix {obj}.{fiix_field}"
                    )

        # vendor_aliases: plenum column → [Fiix field names] (same Fiix name kept across objects)
        vendor_aliases: Dict[str, List[str]] = build_internal_vendor_aliases(tables_by_object)
        field_aliases_by_object = build_field_aliases_by_object(tables_by_object)

        # sample_values_by_field: field_name → sample value
        sample_values_by_field: Dict[str, Any] = {}
        for key, info in fiix_schema.items():
            sv = info.get("sample_value")
            if sv is not None:
                sample_values_by_field[info["field_name"]] = sv

        mapper = {
            "source_system": "Fiix",
            "preserve_fiix_source_names": True,
            "canonical_fields": canonical_fields,
            "vendor_aliases": vendor_aliases,
            "field_aliases_by_object": field_aliases_by_object,
            "tables_by_object": tables_by_object,
            "sample_values_by_field": sample_values_by_field,
            "regex_patterns": {},
            "metadata": {
                "api_version": self.api.API_VERSION,
                "subdomain": self.api.subdomain,
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "api_calls": self.api.request_count,
                "total_objects": len(self._objects),
                "total_fields": len(fiix_schema),
            },
        }

        self.logger.info(
            f"[FiixConnector] Mapper built: {len(canonical_fields)} canonical fields, "
            f"{len(tables_by_object)} object types"
        )
        return mapper


FiixSchemaConnector.FIELD_MAPPINGS = FiixSchemaConnector._legacy_field_mappings()
