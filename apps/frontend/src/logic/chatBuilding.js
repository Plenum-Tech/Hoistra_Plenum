// chatBuilding — which building an attachment in the chat composer is being FILED against.
//
// `run-stateful-with-files` has always accepted a building_id, and the backend gates its
// entire validation half on it (api/routes/workflow.py):
//
//     if building:
//         # each file is checked against the building that was selected first, and a file
//         # that does not clearly belong there is HELD: registered, indexed, not bound,
//         # and put to the uploader as a question
//
// Until now the chat sent only `declForId`, which exactly one path sets — hoisting a
// building and choosing "ingest now". A PDF attached from the composer therefore went up
// with no building: indexed, never validated, never written to the ingestion audit trail,
// and bound to nothing. That is why the Audit trail read empty, and why a contract ingested
// from chat sits attached to none of the company's buildings.
//
// Three rules shape this:
//
//   IT APPEARS ONLY WITH FILES. A question with no attachment is not a filing, and a picker
//   on every turn is noise in the one place the reader types most.
//
//   IT WARNS, IT DOES NOT BLOCK. Attaching a photo to a question is legitimate. What is not
//   legitimate is the consequence being invisible, so the consequence is what it states.
//
//   IT DOES NOT DEFAULT TO THE TOP-BAR SCOPE. That picker sets what the reader is LOOKING
//   AT. Filing a document against it because they happened to be browsing there is exactly
//   the wrong-building binding the backend's validation gate exists to catch.
const CB_DEFAULTS = {
  // The building chosen for the staged attachments, and its name for the label. Held until
  // the conversation ends rather than per-turn: filing three certificates for one building
  // should not mean choosing it three times.
  cbBuildingId: null, cbBuildingName: "",
  cbPickerOpen: false, cbQuery: ""
};

export const chatBuildingMethods = {
  cbOpenPicker() { this.setState({ cbPickerOpen: true, cbQuery: "" }); },
  cbClosePicker() { this.setState({ cbPickerOpen: false, cbQuery: "" }); },
  cbSetQueryText(e) { this.setState({ cbQuery: e && e.target ? e.target.value : String(e || "") }); },

  cbPickBuilding(id, name) {
    this.setState({
      cbBuildingId: id || null,
      cbBuildingName: String(name || ""),
      cbPickerOpen: false,
      cbQuery: "",
      // An explicit choice replaces whatever "hoist a building → ingest now" left behind.
      // The reader has just answered the question; a leftover must not outrank them.
      declForId: id || null,
      declFor: String(name || "")
    });
  },

  cbClearBuilding() {
    this.setState({ cbBuildingId: null, cbBuildingName: "", declForId: null, cbPickerOpen: false, cbQuery: "" });
  },

  // The building this upload should be filed against. An explicit pick wins; otherwise the
  // id "hoist a building → ingest now" set, which was the only route before this existed.
  cbFilingBuildingId() {
    return this.state.cbBuildingId || this.state.declForId || null;
  },

  // Open the existing hoist-a-building form WITHOUT disturbing the staged files — losing
  // them would mean picking the document again after creating the building it belongs to.
  // bcIngestNow() at the end of that form sets declFor/declForId, which cbFilingBuildingId
  // then reads, so a newly hoisted building files the attachment already in the tray.
  cbHoistNew() {
    this.setState({ cbPickerOpen: false, cbQuery: "" });
    if (typeof this.bcOpenForm === "function") this.bcOpenForm();
  }
};

export { CB_DEFAULTS };
