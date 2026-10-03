package xlsxread

import "strings"

// cellFormat is what calamine makes of a number format.
type cellFormat uint8

const (
	fmtOther cellFormat = iota
	fmtDateTime
	fmtTimeDelta
)

// builtinFormat is calamine's builtin_format_by_id, which matches the numFmtId as written.
func builtinFormat(id string) cellFormat {
	switch id {
	case "14", "15", "16", "17", "18", "19", "20", "21", "22", "45", "47":
		return fmtDateTime
	case "46":
		return fmtTimeDelta
	}
	return fmtOther
}

// customFormat is calamine's detect_custom_number_format: quoted text, `\`/`_` escapes and
// `[...]` sections are skipped (except an elapsed [h]/[m]/[s]), only the first `;` section counts,
// and a d/m/h/y/s outside them — or p/m/`/` after an a — means a date.
func customFormat(format string) cellFormat {
	escaped, quote, ap, hms := false, false, false, false
	brackets := 0
	prev := ' '
	isAny := func(r rune, set string) bool { return strings.ContainsRune(set, r) }
	for _, s := range format {
		switch {
		case escaped:
			escaped = false
		case s == '_' || s == '\\':
			escaped = true
		case quote && s == '"':
			quote = false
		case quote:
		case s == '"':
			quote = true
		case s == ';':
			return fmtOther
		case s == '[':
			brackets++
		case s == ']' && brackets == 1 && hms:
			return fmtTimeDelta
		case s == ']':
			if brackets > 0 {
				brackets--
			}
		case (s == 'a' || s == 'A') && !ap && brackets == 0:
			ap = true
		case isAny(s, "pm/PM") && ap && brackets == 0:
			return fmtDateTime
		case isAny(s, "dmhysDMHYS") && !ap && brackets == 0:
			return fmtDateTime
		default:
			if hms && strings.EqualFold(string(s), string(prev)) {
				// still inside [hh] / [mm] / [ss]
			} else {
				hms = prev == '[' && isAny(s, "mhsMHS")
			}
		}
		prev = s
	}
	return fmtOther
}

// styles maps a cell's s="…" index to its format.
type styles struct {
	xf []cellFormat
}

func (st *styles) format(s int) cellFormat {
	if st == nil || s < 0 || s >= len(st.xf) {
		return fmtOther
	}
	return st.xf[s]
}

// formatCodeUnescaped: calamine unescapes a numFmt's formatCode, so a quoted literal written
// &quot;Date: &quot;dd/mm reads as "Date: "dd/mm — a date (the oracle's dates_custom_formats).
const formatCodeUnescaped = true

// parseStyles reads numFmts and cellXfs from xl/styles.xml. A non-empty format the file defines
// wins over the built-in one with the same id (both matched on the id as written).
func parseStyles(data []byte) (*styles, error) {
	custom := map[string]string{}
	st := &styles{}
	sc := scanner{b: data}
	inXfs, done := false, false
	for sc.nextTag() {
		switch {
		case sc.is("styleSheet") && sc.kind != tagStart:
			done = true
		case sc.kind != tagEnd && sc.is("numFmt"):
			id, _ := sc.raw("numFmtId")
			code, _ := sc.raw("formatCode")
			f := string(code)
			if formatCodeUnescaped {
				f = unescape(code)
			}
			if f != "" {
				custom[string(id)] = f
			}
		case sc.is("cellXfs"):
			inXfs = sc.kind == tagStart
		case inXfs && sc.kind != tagEnd && sc.is("xf"):
			id, ok := sc.raw("numFmtId")
			switch f, isCustom := custom[string(id)]; {
			case !ok:
				st.xf = append(st.xf, fmtOther)
			case isCustom:
				st.xf = append(st.xf, customFormat(f))
			default:
				st.xf = append(st.xf, builtinFormat(string(id)))
			}
		}
		if done {
			break
		}
	}
	return st, closed(&sc, done, "styleSheet")
}
