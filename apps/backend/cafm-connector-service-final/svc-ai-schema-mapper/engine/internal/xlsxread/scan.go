package xlsxread

import (
	"bytes"
	"fmt"
	"strconv"
	"strings"
	"unicode/utf8"
)

// A small XML scanner for the OOXML parts: elements, attributes and text, entities decoded,
// namespaces ignored (local names only). The sheet reader runs it once per cell, so a tag's name
// and attributes are byte slices into the part (no allocation); encoding/xml costs several times
// more per cell.

type tagKind uint8

const (
	tagStart tagKind = iota
	tagEnd
	tagEmpty // <x/>: calamine's reader expands it to a start and an end
)

type attr struct{ key, val []byte } // local name, raw (escaped) value

type scanner struct {
	b     []byte
	i     int
	full  []byte // the current tag's name as written
	name  []byte // its local name
	kind  tagKind
	attrs []attr
	err   error // the input ended inside a construct: quick-xml's error, in its words
}

// quick-xml's errors for input that ends inside a construct (calamine reports them as they are).
func syntaxErr(what, close string) error {
	return fmt.Errorf("syntax error: %s not closed: `%s` not found before end of input", what, close)
}

func missingEndTag(name []byte) error {
	return fmt.Errorf("ill-formed document: start tag not closed: `</%s>` not found before end of input", name)
}

func (s *scanner) fail(err error) {
	if s.err == nil {
		s.err = err
	}
	s.i = len(s.b)
}

func localName(n []byte) []byte {
	if i := bytes.IndexByte(n, ':'); i >= 0 {
		return n[i+1:]
	}
	return n
}

// nextTag moves to the next element tag, skipping text, comments, CDATA, processing
// instructions and declarations.
func (s *scanner) nextTag() bool {
	for {
		if s.err != nil {
			return false
		}
		j := indexByteFrom(s.b, '<', s.i)
		if j < 0 {
			s.i = len(s.b)
			return false
		}
		s.i = j
		if s.skipMarkup() {
			continue
		}
		return s.tag()
	}
}

// skipMarkup steps over a comment, CDATA section, processing instruction or declaration at s.i.
func (s *scanner) skipMarkup() bool {
	j := s.i
	var end, what string
	switch {
	case hasPrefixAt(s.b, j, "<!--"):
		end, what = "-->", "comment"
	case hasPrefixAt(s.b, j, "<![CDATA["):
		end, what = "]]>", "CDATA"
	case hasPrefixAt(s.b, j, "<?"):
		end, what = "?>", "processing instruction or xml declaration"
	case hasPrefixAt(s.b, j, "<!"):
		end, what = ">", "DOCTYPE"
	default:
		return false
	}
	k := indexFrom(s.b, end, j+2)
	if k < 0 {
		s.fail(syntaxErr(what, end))
	} else {
		s.i = k + len(end)
	}
	return true
}

func (s *scanner) tag() bool {
	j := s.i + 1
	s.kind = tagStart
	if j < len(s.b) && s.b[j] == '/' {
		s.kind = tagEnd
		j++
	}
	k := j
	for k < len(s.b) && !isSpace(s.b[k]) && s.b[k] != '>' && s.b[k] != '/' {
		k++
	}
	s.full = s.b[j:k]
	s.name = localName(s.full)
	s.attrs = s.attrs[:0]
	for {
		for k < len(s.b) && isSpace(s.b[k]) {
			k++
		}
		if k >= len(s.b) {
			s.fail(syntaxErr("tag", ">"))
			return false
		}
		if s.b[k] == '>' {
			s.i = k + 1
			return true
		}
		if s.b[k] == '/' {
			if k+1 < len(s.b) && s.b[k+1] == '>' {
				s.i = k + 2
				if s.kind == tagStart {
					s.kind = tagEmpty
				}
				return true
			}
			k++
			continue
		}
		a := k
		for k < len(s.b) && s.b[k] != '=' && !isSpace(s.b[k]) && s.b[k] != '>' && s.b[k] != '/' {
			k++
		}
		key := localName(s.b[a:k])
		for k < len(s.b) && isSpace(s.b[k]) {
			k++
		}
		if k >= len(s.b) || s.b[k] != '=' {
			continue // an attribute with no value
		}
		k++
		for k < len(s.b) && isSpace(s.b[k]) {
			k++
		}
		if k >= len(s.b) || (s.b[k] != '"' && s.b[k] != '\'') {
			continue
		}
		q := s.b[k]
		e := indexByteFrom(s.b, q, k+1)
		if e < 0 {
			s.fail(syntaxErr("attribute value", string(q)))
			return false
		}
		s.attrs = append(s.attrs, attr{key, s.b[k+1 : e]})
		k = e + 1
	}
}

func (s *scanner) is(name string) bool { return string(s.name) == name }

func (s *scanner) fullName() []byte { return s.full }

// raw is an attribute's value as written (entities not decoded), and whether it is there.
func (s *scanner) raw(key string) ([]byte, bool) {
	for _, a := range s.attrs {
		if string(a.key) == key {
			return a.val, true
		}
	}
	return nil, false
}

// attr is an attribute's value with entities decoded ("" when it is not there).
func (s *scanner) attr(key string) string {
	v, _ := s.raw(key)
	return unescape(v)
}

// content reads an element's character data up to its end tag (the scanner is just past the
// start tag): text with entities decoded, CDATA sections only when cdata is set; nested elements,
// comments and processing instructions add nothing.
func (s *scanner) content(name string, cdata bool) string {
	open := append([]byte(nil), s.full...)
	var sb strings.Builder
	for s.err == nil {
		if s.i >= len(s.b) {
			s.fail(missingEndTag(open))
			break
		}
		j := indexByteFrom(s.b, '<', s.i)
		if j < 0 {
			j = len(s.b)
		}
		if j > s.i {
			if sb.Len() == 0 && hasPrefixAt(s.b, j, "</") && s.endsAt(j, name) {
				t := unescape(s.b[s.i:j]) // the common case: one text run, then the end tag
				s.i = j
				s.tag()
				return t
			}
			sb.WriteString(unescape(s.b[s.i:j]))
		}
		s.i = j
		if j >= len(s.b) {
			s.fail(missingEndTag(open))
			break
		}
		if hasPrefixAt(s.b, j, "<![CDATA[") {
			k := indexFrom(s.b, "]]>", j+9)
			if k < 0 {
				s.fail(syntaxErr("CDATA", "]]>"))
				break
			}
			if cdata {
				sb.WriteString(normalizeEOL(s.b[j+9 : k]))
			}
			s.i = min(k+3, len(s.b))
			continue
		}
		if s.skipMarkup() {
			continue
		}
		if !s.tag() {
			break
		}
		if s.kind == tagEnd && s.is(name) {
			break
		}
	}
	return sb.String()
}

func (s *scanner) endsAt(j int, name string) bool {
	k := j + 2
	e := k
	for e < len(s.b) && !isSpace(s.b[e]) && s.b[e] != '>' {
		e++
	}
	return string(localName(s.b[k:e])) == name
}

// skip moves past the end of the element whose start tag was just read.
func (s *scanner) skip() {
	if s.kind != tagStart {
		return
	}
	name := string(s.name)
	depth := 1
	for s.nextTag() {
		if !s.is(name) {
			continue
		}
		switch s.kind {
		case tagStart:
			depth++
		case tagEnd:
			if depth--; depth == 0 {
				return
			}
		}
	}
}

func isSpace(c byte) bool { return c == ' ' || c == '\t' || c == '\n' || c == '\r' }

func indexByteFrom(b []byte, c byte, from int) int {
	if from >= len(b) {
		return -1
	}
	if i := bytes.IndexByte(b[from:], c); i >= 0 {
		return from + i
	}
	return -1
}

func indexFrom(b []byte, sub string, from int) int {
	if from > len(b) {
		return -1
	}
	i := bytes.Index(b[from:], []byte(sub))
	if i < 0 {
		return -1
	}
	return from + i
}

func hasPrefixAt(b []byte, i int, p string) bool {
	return len(b)-i >= len(p) && string(b[i:i+len(p)]) == p
}

func atoi(s string) (int, bool) {
	n, err := strconv.Atoi(strings.TrimSpace(s))
	return n, err == nil
}

func normalizeEOL(b []byte) string {
	if bytes.IndexByte(b, '\r') < 0 {
		return string(b)
	}
	var sb strings.Builder
	for i := 0; i < len(b); i++ {
		if b[i] == '\r' {
			sb.WriteByte('\n')
			if i+1 < len(b) && b[i+1] == '\n' {
				i++
			}
			continue
		}
		sb.WriteByte(b[i])
	}
	return sb.String()
}

// unescape decodes XML entities (the five named ones and &#…; / &#x…;); a raw "\r\n" or lone
// "\r" reads as "\n", as XML line-end handling makes it (calamine returns them so). An entity
// it cannot read stays as written.
func unescape(b []byte) string {
	if bytes.IndexByte(b, '&') < 0 && bytes.IndexByte(b, '\r') < 0 {
		return string(b)
	}
	var sb strings.Builder
	for i := 0; i < len(b); i++ {
		c := b[i]
		if c == '\r' {
			sb.WriteByte('\n')
			if i+1 < len(b) && b[i+1] == '\n' {
				i++
			}
			continue
		}
		if c != '&' {
			sb.WriteByte(c)
			continue
		}
		semi := indexByteFrom(b, ';', i+1)
		if semi < 0 || semi-i > 12 {
			sb.WriteByte(c)
			continue
		}
		ent := string(b[i+1 : semi])
		switch {
		case ent == "amp":
			sb.WriteByte('&')
		case ent == "lt":
			sb.WriteByte('<')
		case ent == "gt":
			sb.WriteByte('>')
		case ent == "quot":
			sb.WriteByte('"')
		case ent == "apos":
			sb.WriteByte('\'')
		case strings.HasPrefix(ent, "#x") || strings.HasPrefix(ent, "#X"):
			n, err := strconv.ParseUint(ent[2:], 16, 32)
			if err != nil || !utf8.ValidRune(rune(n)) {
				sb.WriteByte(c)
				continue
			}
			sb.WriteRune(rune(n))
		case strings.HasPrefix(ent, "#"):
			n, err := strconv.ParseUint(ent[1:], 10, 32)
			if err != nil || !utf8.ValidRune(rune(n)) {
				sb.WriteByte(c)
				continue
			}
			sb.WriteRune(rune(n))
		default:
			sb.WriteByte(c)
			continue
		}
		i = semi
	}
	return sb.String()
}
