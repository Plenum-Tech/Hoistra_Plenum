// Package csvread reads delimited text the way ingest does: pandas' C tokenizer (read_csv with the
// default dialect, dtype=str) and ingest_node._sanitize_column_names.
package csvread

import (
	"errors"
	"fmt"
	"strings"
)

// Delimiter is ingest_node._detect_delimiter: in the first five lines of the first 4,096
// characters, the first of , \t ; | that occurs at all, else a comma.
func Delimiter(text string) byte {
	if n := 0; true {
		for i := range text {
			if n == 4096 {
				text = text[:i]
				break
			}
			n++
		}
	}
	lines := strings.SplitN(text, "\n", 6)
	if len(lines) > 5 {
		lines = lines[:5]
	}
	for _, d := range []byte{',', '\t', ';', '|'} {
		for _, l := range lines {
			if strings.IndexByte(l, d) >= 0 {
				return d
			}
		}
	}
	return ','
}

// record is one tokenized line: its fields and the file line pandas reports it as (record ends and
// skipped blank lines before it, plus one; a newline inside quotes is not a line end).
type record struct {
	fields []string
	line   int
}

// tokenizer states, as pandas' tokenizer.c names them.
const (
	startRecord = iota
	startField
	inField
	inQuotedField
	quoteInQuotedField
	eatCRNL
	whitespaceLine
)

var errEOFInQuotes = errors.New("eof in quotes")

// tokenize runs tokenizer.c's state machine for the default dialect (`"` quoting with doubled
// quotes, no escape character, skip_blank_lines=True). A NUL ends its field's text, as the C
// tokenizer's string handling does.
func tokenize(text string, delim byte) ([]record, int, error) {
	var recs []record
	var fields []string
	var field []byte
	lines := 0     // file_lines: record ends and skipped lines so far
	quoteRow := -1 // the record a quoted field started in
	lineStart := 0 // where the current physical line began (for the whitespace backtrack)
	state := startRecord
	endField := func() {
		f := field
		if i := indexNUL(f); i >= 0 {
			f = f[:i]
		}
		fields = append(fields, string(f))
		field = field[:0]
	}
	endRecord := func() {
		recs = append(recs, record{fields: fields, line: lines + 1})
		fields = nil
		lines++
	}
	for i := 0; i < len(text); i++ {
		c := text[i]
		switch state {
		case startRecord:
			switch {
			case c == '\n':
				lines++ // a blank line, skipped
				continue
			case c == '\r':
				lines++
				state = eatCRNL
				continue
			case (c == ' ' || c == '\t') && c != delim:
				lineStart = i
				state = whitespaceLine
				continue
			}
			lineStart = i
			state = startField
			i-- // the same byte starts the field
		case whitespaceLine:
			switch {
			case c == '\n':
				lines++
				state = startRecord
			case c == '\r':
				lines++
				state = eatCRNL
			case (c == ' ' || c == '\t') && c != delim:
			default:
				i = lineStart - 1 // not blank after all: its spaces are data
				state = startField
			}
		case startField:
			switch {
			case c == '\n':
				endField()
				endRecord()
				state = startRecord
			case c == '\r':
				endField()
				endRecord()
				state = eatCRNL
			case c == '"':
				quoteRow = len(recs)
				state = inQuotedField
			case c == delim:
				endField()
			default:
				field = append(field, c)
				state = inField
			}
		case inField:
			switch {
			case c == '\n':
				endField()
				endRecord()
				state = startRecord
			case c == '\r':
				endField()
				endRecord()
				state = eatCRNL
			case c == delim:
				endField()
				state = startField
			default:
				field = append(field, c)
			}
		case inQuotedField:
			if c == '"' {
				state = quoteInQuotedField
			} else {
				field = append(field, c)
			}
		case quoteInQuotedField:
			switch {
			case c == '"':
				field = append(field, '"')
				state = inQuotedField
			case c == delim:
				endField()
				state = startField
			case c == '\n':
				endField()
				endRecord()
				state = startRecord
			case c == '\r':
				endField()
				endRecord()
				state = eatCRNL
			default:
				field = append(field, c)
				state = inField
			}
		case eatCRNL:
			state = startRecord
			if c != '\n' {
				i-- // a lone \r ended the line; this byte starts the next one
			}
		}
	}
	switch state {
	case inQuotedField:
		return recs, quoteRow, errEOFInQuotes
	case startField, inField, quoteInQuotedField:
		endField()
		endRecord()
	}
	return recs, -1, nil
}

func indexNUL(b []byte) int {
	for i, c := range b {
		if c == 0 {
			return i
		}
	}
	return -1
}

func tokenizingError(format string, a ...any) error {
	return fmt.Errorf("Error tokenizing data. C error: "+format, a...)
}
