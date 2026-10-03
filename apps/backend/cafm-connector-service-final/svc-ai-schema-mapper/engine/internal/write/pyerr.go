package write

import (
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/jackc/pgx/v5/pgconn"

	"hoistra/engine/internal/pystr"
)

//go:embed sqlstates.json
var sqlstatesJSON []byte

// sqlstateClass is asyncpg 0.31's SQLSTATE → exception class map (PostgresMessageMeta._message_map).
var sqlstateClass = func() map[string]string {
	m := map[string]string{}
	if err := json.Unmarshal(sqlstatesJSON, &m); err != nil {
		panic(err)
	}
	return m
}()

// pyErr is a failed statement as the Python writer sees it: the SQLAlchemy exception's class name
// and str(), and str() of the asyncpg error it wraps. The writer reports a row by the first 220
// characters of str(), counts "identical" failures by class and the first 120, and finds an orphan
// foreign key in the asyncpg text — so the engine builds the same strings.
type pyErr struct {
	typ  string
	orig string
	str  string
}

// sqlalchemyClass is how SQLAlchemy's asyncpg dialect wraps an asyncpg error class: the dialect's
// DBAPI class, the sqlalchemy.exc class it becomes, and its "Background on this error" code.
func sqlalchemyClass(code string) (dbapi, typ, url string) {
	switch {
	case strings.HasPrefix(code, "23"):
		return "IntegrityError", "IntegrityError", "gkpj"
	case strings.HasPrefix(code, "42"):
		return "ProgrammingError", "ProgrammingError", "f405"
	case code == "XX000":
		return "InternalServerError", "InternalError", "2j85"
	}
	return "Error", "DBAPIError", "dbapi"
}

func newPyErr(code, message, detail, hint, sql string, params []pyVal) pyErr {
	cls, ok := sqlstateClass[code]
	module := "asyncpg.exceptions"
	if !ok {
		cls, module = "UnknownPostgresError", "asyncpg.exceptions._base"
	}
	msg := message
	if detail != "" {
		msg += "\nDETAIL:  " + detail
	}
	if hint != "" {
		msg += "\nHINT:  " + hint
	}
	dbapi, typ, url := sqlalchemyClass(code)
	orig := fmt.Sprintf("<class '%s.%s'>: %s", module, cls, msg)
	str := fmt.Sprintf("(sqlalchemy.dialects.postgresql.asyncpg.%s) %s\n[SQL: %s]", dbapi, orig, sql)
	if len(params) > 0 {
		str += "\n[parameters: " + reprParams(params) + "]"
	}
	str += "\n(Background on this error at: https://sqlalche.me/e/20/" + url + ")"
	return pyErr{typ: typ, orig: orig, str: str}
}

// pgPyErr is a statement Postgres rejected.
func pgPyErr(err error, sql string, params []pyVal) (pyErr, bool) {
	var pe *pgconn.PgError
	if !errors.As(err, &pe) {
		return pyErr{}, false
	}
	return newPyErr(pe.Code, pe.Message, pe.Detail, pe.Hint, sql, params), true
}

// bindPyErr is asyncpg refusing parameter pos (1-based) before anything was sent.
func bindPyErr(pos int, v pyVal, cause, sql string, params []pyVal) pyErr {
	r := v.repr()
	if rs := []rune(r); len(rs) > 40 {
		r = string(rs[:40]) + "..."
	}
	msg := fmt.Sprintf("invalid input for query argument $%d: %s (%s)", pos, r, cause)
	return newPyErr("22000", msg, "", "", sql, params)
}

// reprParams is SQLAlchemy's "[parameters: …]": the positional tuple, each value's repr cut to
// 300 characters the way sql_util._repr_params cuts it.
func reprParams(params []pyVal) string {
	parts := make([]string, len(params))
	for i, p := range params {
		r := p.repr()
		if rs := []rune(r); len(rs) > 300 {
			r = string(rs[:150]) + fmt.Sprintf(" ... (%d characters truncated) ... ", len(rs)-300) + string(rs[len(rs)-150:])
		}
		parts[i] = r
	}
	if len(parts) == 1 {
		return "(" + parts[0] + ",)"
	}
	return "(" + strings.Join(parts, ", ") + ")"
}

func runes(s string, n int) string {
	if rs := []rune(s); len(rs) > n {
		return string(rs[:n])
	}
	return s
}

// sig is _insert_rows' streak signature: f"{type(row_exc).__name__}:{str(row_exc)[:120]}".
func (e pyErr) sig() string { return e.typ + ":" + runes(e.str, 120) }

var fkKeyRE = regexp.MustCompile(`Key \(([^)]+)\)=`)

// fkColumns is _foreign_key_columns_from_error: the columns of a foreign-key violation, read from
// the text of the asyncpg error.
func (e pyErr) fkColumns() []string {
	low := strings.ToLower(e.orig)
	if !strings.Contains(low, "foreign key") && !strings.Contains(low, "is not present in table") {
		return nil
	}
	m := fkKeyRE.FindStringSubmatch(e.orig)
	if m == nil {
		return nil
	}
	var out []string
	for _, c := range strings.Split(m[1], ",") {
		if c = pystr.Strip(c); c != "" {
			out = append(out, strings.Trim(c, `"`))
		}
	}
	return out
}
