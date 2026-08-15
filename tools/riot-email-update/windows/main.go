// RiotEmailUpdate.exe — one-click Windows launcher for the Riot email-update tool.
//
// Double-click (or run from a console) to:
//   1. Find Python 3 on this machine
//   2. Run launch.py (venv + browser setup, then data/tasks.csv batch)
//
// Build (from Linux or Windows):
//   GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o ../RiotEmailUpdate.exe .
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func main() {
	root, err := resolveRoot()
	if err != nil {
		fatal("%v", err)
	}
	if err := os.Chdir(root); err != nil {
		fatal("cannot chdir to %s: %v", root, err)
	}

	launchPy := filepath.Join(root, "launch.py")
	if _, err := os.Stat(launchPy); err != nil {
		fatal(
			"launch.py not found next to RiotEmailUpdate.exe.\n"+
				"  Put the .exe in the tools/riot-email-update folder\n"+
				"  (same folder as launch.py and run_tasks.py).\n"+
				"  Looking in: %s",
			root,
		)
	}

	python, err := findPython(root)
	if err != nil {
		fmt.Fprintln(os.Stderr, err.Error())
		pause()
		os.Exit(1)
	}

	fmt.Println("== RiotEmailUpdate.exe ==")
	fmt.Printf("[launcher] python=%s\n", python)
	fmt.Printf("[launcher] root=%s\n", root)
	fmt.Println()

	args := append([]string{launchPy}, os.Args[1:]...)
	cmd := exec.Command(python, args...)
	cmd.Dir = root
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			fmt.Fprintf(os.Stderr, "\n[launcher] exit code %d\n", ee.ExitCode())
			pause()
			os.Exit(ee.ExitCode())
		}
		fmt.Fprintf(os.Stderr, "\n[launcher] failed: %v\n", err)
		pause()
		os.Exit(1)
	}
	pause()
}

func resolveRoot() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", fmt.Errorf("cannot resolve executable path: %w", err)
	}
	exe, err = filepath.EvalSymlinks(exe)
	if err != nil {
		return "", fmt.Errorf("cannot resolve executable path: %w", err)
	}
	root := filepath.Dir(exe)
	if _, err := os.Stat(filepath.Join(root, "launch.py")); err == nil {
		return root, nil
	}
	// `go run` uses a temp binary dir — fall back to CWD.
	cwd, err := os.Getwd()
	if err == nil {
		if _, err := os.Stat(filepath.Join(cwd, "launch.py")); err == nil {
			return cwd, nil
		}
	}
	return root, nil
}

func findPython(root string) (string, error) {
	// Prefer an existing venv so we don't require a global interpreter after setup.
	venv := filepath.Join(root, ".venv", "Scripts", "python.exe")
	if st, err := os.Stat(venv); err == nil && !st.IsDir() {
		return venv, nil
	}

	candidates := [][]string{
		{"py", "-3"},
		{"python"},
		{"python3"},
	}
	for _, c := range candidates {
		path, err := exec.LookPath(c[0])
		if err != nil {
			continue
		}
		args := append([]string{}, c[1:]...)
		args = append(args, "-c", "import sys; print(sys.executable)")
		out, err := exec.Command(path, args...).Output()
		if err != nil {
			continue
		}
		exe := strings.TrimSpace(string(out))
		if exe == "" {
			continue
		}
		if _, err := os.Stat(exe); err == nil {
			return exe, nil
		}
		return path, nil
	}

	return "", fmt.Errorf(
		"Python 3 was not found on PATH.\n\n" +
			"Install Python 3.11+ from https://www.python.org/downloads/\n" +
			"  • Check \"Add python.exe to PATH\"\n" +
			"  • Check \"Install py launcher\"\n" +
			"Then double-click RiotEmailUpdate.exe again.\n",
	)
}

func pause() {
	// Keep the console open after a double-click so errors are readable.
	if fileInfo, err := os.Stdout.Stat(); err == nil {
		if fileInfo.Mode()&os.ModeCharDevice != 0 {
			fmt.Print("\nPress Enter to close…")
			var b [1]byte
			_, _ = os.Stdin.Read(b[:])
		}
	}
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	pause()
	os.Exit(1)
}
