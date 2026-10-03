package cell

import "testing"

func TestPythonTruthinessAndStr(t *testing.T) {
	cases := []struct {
		c      Cell
		truthy bool
		str    string
		empty  bool
	}{
		{None, false, "None", true},
		{Of(""), false, "", true},
		{Of(" "), true, " ", false},
		{Of("0"), true, "0", false},
		{OfInt(0), false, "0", false},
		{OfInt(7), true, "7", false},
		{OfBool(true), true, "True", false},
		{OfBool(false), false, "False", false},
	}
	for _, x := range cases {
		if x.c.Truthy() != x.truthy || x.c.PyStr() != x.str || x.c.Empty() != x.empty {
			t.Errorf("%+v: truthy=%v str=%q empty=%v", x.c, x.c.Truthy(), x.c.PyStr(), x.c.Empty())
		}
	}
	if !None.IsNone() || Of("").IsNone() {
		t.Error("IsNone")
	}
}
