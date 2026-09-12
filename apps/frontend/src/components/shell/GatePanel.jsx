// GatePanel — the sign-in column of the gate: sign in, create an account, enter a code,
// forgotten and reset password. One panel, five modes, driven by vals.authMode (logic/auth.js).
// Server messages are shown as the server wrote them. `vals` is the view model from useHoistra().
import React from 'react';

const KICKER = { fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const H2 = { fontSize: "26px", margin: "12px 0 0", lineHeight: "1.15" };
const BLURB = { fontSize: "12.5px", lineHeight: "1.55", color: "var(--color-neutral-400)", margin: "9px 0 0" };
const INPUT = { width: "100%", boxSizing: "border-box", fontSize: "14px", padding: "11px 13px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" };
const PRIMARY = { textAlign: "center", padding: "11px", borderRadius: "8px", background: "var(--color-text)", color: "var(--color-bg)", fontSize: "14px", cursor: "pointer" };
const SECONDARY = { textAlign: "center", padding: "11px", borderRadius: "8px", border: "1px solid var(--color-divider)", fontSize: "13.5px", color: "var(--color-neutral-300)", cursor: "pointer" };
const DISABLED = { opacity: 0.55, cursor: "default", pointerEvents: "none" };
const LINK = { fontSize: "11.5px", color: "var(--color-neutral-400)", cursor: "pointer" };
const HINT = { fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5", marginTop: "-3px" };
const NOTE = { fontSize: "12px", lineHeight: "1.5", color: "var(--color-neutral-300)", marginTop: "14px" };
const ERROR = { fontSize: "12px", lineHeight: "1.5", color: "var(--st-risk)", marginTop: "12px" };

const COPY = {
  signin:   { kicker: "Member access", title: "Sign in", blurb: "Your hoisted portfolio is waiting. The reading is done. The decisions are yours." },
  register: { kicker: "New account", title: "Create an account", blurb: "Set a password you can remember. You'll confirm your email with a six-digit code." },
  verify:   { kicker: "Confirm your email", title: "Enter the code", blurb: null },
  forgot:   { kicker: "Password reset", title: "Forgot your password?", blurb: "Enter your email and we'll send a code to reset it." },
  reset:    { kicker: "Password reset", title: "Set a new password", blurb: null }
};

function Field(p) {
  return <input className="input" type={p.type || "text"} value={p.value} onChange={p.onChange} onKeyDown={p.onKey} placeholder={p.placeholder} autoComplete={p.auto} inputMode={p.inputMode} style={INPUT} />;
}

function Button(p) {
  const base = p.secondary ? SECONDARY : PRIMARY;
  return <div className={p.secondary ? "hv4" : "hv3"} onClick={p.disabled ? undefined : p.onClick} style={p.disabled ? { ...base, ...DISABLED } : base}>{p.label}</div>;
}

export default function GatePanel({ vals }) {
  const m = vals.authMode;
  const c = COPY[m] || COPY.signin;
  const busy = vals.authBusy;
  const codeScreen = m === "verify" || m === "reset";
  const blurb = codeScreen ? "Sent to " + (vals.email || "your email") + "." : c.blurb;
  const resendLabel = busy ? "Sending…" : vals.authCoolingDown ? "Resend code · " + vals.authCountdown : "Resend code";
  const minHint = "At least " + vals.authMinLength + " characters — a memorable phrase beats a short one with symbols.";
  const codePh = vals.authCodeLength + "-digit code";
  return (
    <div style={{ width: "100%" }}>
      <div style={KICKER}>{c.kicker}</div>
      <h2 style={H2}>{c.title}</h2>
      <p style={BLURB}>{blurb}</p>
      {vals.authNotice ? <div style={NOTE}>{vals.authNotice}</div> : null}
      <div style={{ display: "flex", flexDirection: "column", gap: "9px", marginTop: vals.authNotice ? "16px" : "26px" }}>
        {m === "signin" ? <>
          <Field value={vals.email} onChange={vals.setEmail} onKey={vals.authKey} placeholder="you@portfolio.com" auto="username" type="email" />
          <Field value={vals.password} onChange={vals.setPassword} onKey={vals.authKey} placeholder="Password" auto="current-password" type="password" />
          <Button label={busy ? "Signing in…" : "Continue"} onClick={vals.authSignIn} disabled={busy} />
          <Button label="Single sign-on" onClick={vals.authSSO} secondary />
        </> : null}
        {m === "register" ? <>
          <Field value={vals.fullName} onChange={vals.setFullName} onKey={vals.authKey} placeholder="Full name" auto="name" />
          <Field value={vals.email} onChange={vals.setEmail} onKey={vals.authKey} placeholder="you@portfolio.com" auto="email" type="email" />
          <Field value={vals.password} onChange={vals.setPassword} onKey={vals.authKey} placeholder="Password" auto="new-password" type="password" />
          <div style={HINT}>{minHint}</div>
          <Field value={vals.phone} onChange={vals.setPhone} onKey={vals.authKey} placeholder="Phone (optional)" auto="tel" type="tel" />
          <Button label={busy ? "Creating…" : "Create account"} onClick={vals.authRegister} disabled={busy} />
        </> : null}
        {m === "verify" ? <>
          <Field value={vals.code} onChange={vals.setCode} onKey={vals.authKey} placeholder={codePh} auto="one-time-code" inputMode="numeric" />
          <Button label={busy ? "Confirming…" : "Confirm"} onClick={vals.authVerify} disabled={busy} secondary={vals.authDeadCode} />
          <Button label={resendLabel} onClick={vals.authResend} disabled={busy || vals.authCoolingDown} secondary={!vals.authDeadCode} />
        </> : null}
        {m === "forgot" ? <>
          <Field value={vals.email} onChange={vals.setEmail} onKey={vals.authKey} placeholder="you@portfolio.com" auto="email" type="email" />
          <Button label={busy ? "Sending…" : "Send reset code"} onClick={vals.authForgot} disabled={busy} />
        </> : null}
        {m === "reset" ? <>
          <Field value={vals.code} onChange={vals.setCode} onKey={vals.authKey} placeholder={codePh} auto="one-time-code" inputMode="numeric" />
          <Field value={vals.newPassword} onChange={vals.setNewPassword} onKey={vals.authKey} placeholder="New password" auto="new-password" type="password" />
          <div style={HINT}>{minHint}</div>
          <Button label={busy ? "Setting…" : "Set password"} onClick={vals.authReset} disabled={busy} secondary={vals.authDeadCode} />
          <Button label={resendLabel} onClick={vals.authResend} disabled={busy || vals.authCoolingDown} secondary={!vals.authDeadCode} />
        </> : null}
      </div>
      {vals.authError ? <div style={ERROR}>{vals.authError}</div> : null}
      {vals.authLocked ? (
        <div style={{ ...NOTE, marginTop: "6px" }}>
          {"Unlocks in " + vals.authCountdown + " · "}
          <span className="hv6" onClick={vals.authGoForgot} style={{ ...LINK, color: "var(--color-text)" }}>{"Reset your password"}</span>
        </div>
      ) : null}
      <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", marginTop: "14px" }}>
        {m === "signin" ? <>
          <span className="hv6" onClick={vals.authGoForgot} style={LINK}>{"Forgot password?"}</span>
          {vals.authCanRegister ? <span className="hv6" onClick={vals.authGoRegister} style={LINK}>{"Create an account"}</span> : null}
        </> : <span className="hv6" onClick={vals.authGoSignin} style={LINK}>{"Back to sign in"}</span>}
      </div>
      <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.55", marginTop: "20px" }}>
        {"No FM cooperation required. Meter consent is captured at onboarding for MPAN and MPRN feeds."}
      </div>
      <a href="Hoistway Customer Journey.dc.html" style={{ fontSize: "11.5px", display: "inline-block", marginTop: "22px" }}>
        {"Read the customer journey →"}
      </a>
    </div>
  );
}
