// hoist-engine does the row-heavy steps of a Hoistra migration for the schema-mapper.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"runtime"
	"runtime/debug"
	"sort"
	"syscall"

	"hoistra/engine/internal/protocol"
)

const Version = "0.1.0"

// Command runs one engine command: it reads its job file and returns the result payload.
type Command func(ctx context.Context, jobPath string, em *protocol.Emitter) (any, error)

var commands = map[string]Command{}

func register(name string, fn Command) { commands[name] = fn }

func init() {
	register("version", func(context.Context, string, *protocol.Emitter) (any, error) {
		names := make([]string, 0, len(commands))
		for n := range commands {
			if n != "version" {
				names = append(names, n)
			}
		}
		sort.Strings(names)
		return map[string]any{"version": Version, "go": runtime.Version(), "commands": names}, nil
	})
}

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

func run(args []string, stdout, stderr io.Writer) int {
	em := protocol.NewEmitter(stdout)
	if len(args) == 0 {
		em.Error(protocol.CodeBadJob, "usage: hoist-engine <command> --job <file>")
		return 2
	}
	cmd, ok := commands[args[0]]
	if !ok {
		em.Error(protocol.CodeBadJob, "unknown command "+args[0])
		return 2
	}
	fs := flag.NewFlagSet(args[0], flag.ContinueOnError)
	fs.SetOutput(stderr)
	job := fs.String("job", "", "path to the job JSON")
	check := fs.Bool("check", false, "decode the job against the command's own job and stop")
	if err := fs.Parse(args[1:]); err != nil {
		em.Error(protocol.CodeBadJob, err.Error())
		return 2
	}
	protocol.CheckOnly = *check
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()
	res, err := safeRun(ctx, cmd, *job, em, stderr)
	if errors.Is(err, protocol.ErrJobChecked) {
		res, err = map[string]any{"job": "ok"}, nil
	}
	if err != nil {
		code, msg := protocol.Classify(ctx, err)
		em.Error(code, msg)
		return 1
	}
	if err := em.Result(res); err != nil {
		fmt.Fprintln(stderr, "could not write result:", err)
		return 1
	}
	return 0
}

func safeRun(ctx context.Context, cmd Command, job string, em *protocol.Emitter, stderr io.Writer) (res any, err error) {
	defer func() {
		if r := recover(); r != nil {
			fmt.Fprintf(stderr, "panic: %v\n%s", r, debug.Stack())
			err = protocol.Errorf(protocol.CodeInternal, "engine panic: %v", r)
		}
	}()
	return cmd(ctx, job, em)
}
