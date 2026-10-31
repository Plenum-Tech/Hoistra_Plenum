"""One pure, versioned date interpretation; never mutates vendor state."""
from datetime import date


def classify(values, today: date):
    anchor = values.get('expiry_date')
    basis = 'expiry_date'
    if not anchor and values.get('review_date') and values.get('review_rule'):
        anchor, basis = values['review_date'], 'documented_review_date'
    if not anchor:
        return {'days_to_expiry': None, 'expiry_label': 'unknown', 'date_basis': None,
                'assessment_date': today.isoformat(), 'action_window': False}
    days = (date.fromisoformat(str(anchor)) - today).days
    if days < 0:
        label = 'expired/lapsed'
    elif days == 0:
        label = 'expires today'
    elif days <= 7:
        label = 'critical'
    elif days <= 30:
        label = 'action-required'
    elif days <= 90:
        label = 'advance reminder'
    else:
        label = 'current'
    return {'days_to_expiry': days, 'expiry_label': label, 'date_basis': basis,
            'assessment_date': today.isoformat(), 'action_window': 0 <= days <= 30}
