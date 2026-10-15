package xlsxread

import (
	"bytes"
	"fmt"
	"regexp"
	"unicode/utf8"

	"golang.org/x/text/encoding/htmlindex"
)

// declaration is the encoding a part's XML declaration names, if any.
var declaration = regexp.MustCompile(`\A<\?xml[^>]*?\bencoding\s*=\s*["']([^"']*)["']`)

func encodingErr(name string) error {
	return fmt.Errorf("XML encoding error: cannot decode input using %s", name)
}

// decodePart is a part as quick-xml decodes it for calamine (probed with python-calamine): a
// UTF-16 byte-order mark is refused, an encoding its declaration names is decoded by its WHATWG
// label (encoding_rs's labels: ISO-8859-1 reads as windows-1252), and text that is then not valid
// UTF-8 is refused — the scanner and the Arrow files only ever see UTF-8.
func decodePart(b []byte) ([]byte, error) {
	switch {
	case bytes.HasPrefix(b, []byte{0xFF, 0xFE}):
		return nil, encodingErr("UTF-16LE")
	case bytes.HasPrefix(b, []byte{0xFE, 0xFF}):
		return nil, encodingErr("UTF-16BE")
	}
	b = bytes.TrimPrefix(b, []byte{0xEF, 0xBB, 0xBF})
	if m := declaration.FindSubmatch(b); m != nil {
		enc, err := htmlindex.Get(string(m[1]))
		if err != nil {
			return nil, fmt.Errorf("XML encoding error: unsupported encoding `%s`", m[1])
		}
		name, _ := htmlindex.Name(enc)
		switch name {
		case "utf-8":
		case "utf-16le":
			return nil, encodingErr("UTF-16LE")
		case "utf-16be":
			return nil, encodingErr("UTF-16BE")
		default:
			out, err := enc.NewDecoder().Bytes(b)
			if err != nil {
				return nil, encodingErr(name)
			}
			b = out
		}
	}
	if !utf8.Valid(b) {
		return nil, encodingErr("UTF-8")
	}
	return b, nil
}
