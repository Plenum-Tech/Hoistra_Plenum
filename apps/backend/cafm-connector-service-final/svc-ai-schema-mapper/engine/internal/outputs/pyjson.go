package outputs

import (
	"bufio"
	"strconv"
	"strings"
	"unicode/utf16"

	"hoistra/engine/internal/cell"
)

const hexDigits = "0123456789abcdef"

// writeJSONString is json.dumps(str) with ensure_ascii=True (CPython's ascii_escape_unicode).
func writeJSONString(w *bufio.Writer, s string) {
	w.WriteByte('"')
	for _, r := range s {
		switch {
		case r == '"':
			w.WriteString(`\"`)
		case r == '\\':
			w.WriteString(`\\`)
		case r == '\n':
			w.WriteString(`\n`)
		case r == '\r':
			w.WriteString(`\r`)
		case r == '\t':
			w.WriteString(`\t`)
		case r == '\b':
			w.WriteString(`\b`)
		case r == '\f':
			w.WriteString(`\f`)
		case r < 0x20:
			writeU(w, r)
		case r < 0x80:
			w.WriteByte(byte(r))
		case r <= 0xffff:
			writeU(w, r)
		default:
			a, b := utf16.EncodeRune(r)
			writeU(w, a)
			writeU(w, b)
		}
	}
	w.WriteByte('"')
}

func writeU(w *bufio.Writer, r rune) {
	w.WriteString(`\u`)
	w.WriteByte(hexDigits[(r>>12)&0xf])
	w.WriteByte(hexDigits[(r>>8)&0xf])
	w.WriteByte(hexDigits[(r>>4)&0xf])
	w.WriteByte(hexDigits[r&0xf])
}

func writeJSONCell(w *bufio.Writer, c cell.Cell) {
	switch c.K {
	case cell.Null:
		w.WriteString("null")
	case cell.Int:
		w.WriteString(strconv.FormatInt(c.I, 10))
	case cell.Bool:
		if c.I != 0 {
			w.WriteString("true")
		} else {
			w.WriteString("false")
		}
	default:
		writeJSONString(w, c.S)
	}
}

// indent writes json.dumps(indent=2)'s newline and indentation for nesting depth d.
func indent(w *bufio.Writer, d int) {
	w.WriteByte('\n')
	w.WriteString(strings.Repeat("  ", d))
}
