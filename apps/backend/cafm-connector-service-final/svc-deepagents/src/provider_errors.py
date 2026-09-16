"""Telling "the model provider refused our key" apart from every other failure.

Both provider keys were refused on the local stack:

    POST https://api.anthropic.com/v1/messages -> 401  "API key is invalid."
    POST https://api.openai.com/v1/chat/completions -> 401
      "You do not have access to the organization tied to the API key."  invalid_organization

The orchestrator had no model at all, /run-stateful answered 500, and the provider's own
sentence was copied into the response body — so a failing report card stored, on a record a
user reads:

    orchestrator answered 500: {"detail":"Error code: 401 - {'error': {'message': 'You do not
    have access to the organization tied to the API key.'…

Two things are wrong with that. A 500 says this service broke, when this service is fine and a
dependency will not serve it; and the upstream text belongs in a log, not in an answer.

Kept in its own module, with no imports, so the classification can be used by anything that
needs it and tested without standing up the app.
"""

from __future__ import annotations

#: Matched on the providers' own error CODES, not their prose. Wording changes between
#: releases; `invalid_organization` and `authentication_error` do not.
_REFUSED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("anthropic", ("authentication_error", "api key is invalid", "invalid x-api-key")),
    ("openai", ("invalid_organization", "invalid_api_key", "incorrect api key")),
)

#: A key that is right but has nothing left to spend. Same symptom, different fix — and
#: telling someone to rotate a credential that was never wrong wastes an afternoon.
_OUT_OF_CREDIT: tuple[str, ...] = ("insufficient_quota", "credit balance is too low", "billing")


def provider_refusal(error: str | None) -> tuple[str, str] | None:
    """``(provider, kind)`` when the model provider will not serve us, else ``None``.

    ``kind`` is ``"rejected"`` or ``"out of credit"``. ``provider`` is the one named by the
    error, or ``"model provider"`` when the message says the account is out of credit without
    saying whose account.

    The two are detected INDEPENDENTLY, which is the whole reason this is a function and not a
    lookup. An earlier version only looked for out-of-credit once a provider's auth marker had
    already matched — and a quota error carries no auth marker, so it fell through to a generic
    500 and the one failure a person could fix in a minute looked like a crash.

    Returning None for everything else is the important half: a timeout, a tool error or a
    badly-formed question must never be reported as a bad API key, because that sends somebody
    to rotate a credential that was never the problem.
    """
    e = (error or "").lower()
    out_of_credit = any(q in e for q in _OUT_OF_CREDIT)
    named = next((p for p, needles in _REFUSED if any(n in e for n in needles)), None)
    if named:
        return named, ("out of credit" if out_of_credit else "rejected")
    if out_of_credit:
        # Whose account is not always stated. "Out of credit" is still actionable; guessing
        # the provider is not, and naming the wrong one costs more than naming none.
        return "model provider", "out of credit"
    return None


def refusal_detail(provider: str, kind: str) -> dict[str, object]:
    """The body to answer with — names the provider and the variable, carries no upstream text."""
    return {
        "ok": False,
        "reason": f"model_provider_{kind.replace(' ', '_')}",
        "provider": provider,
        "error": (f"The {provider} API {kind} this deployment's credentials, so no model "
                  f"could answer. This is a configuration problem, not a problem with the "
                  f"question — check "
                  + (f"{provider.upper()}_API_KEY for this environment."
                     if provider != "model provider"
                     else "the model provider credentials for this environment.")),
    }
