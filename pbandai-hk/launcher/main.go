package main

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

func main() {
	oneClick := hasFlag("--one-click") || hasFlag("-y")
	setupOnly := hasFlag("--setup-only")
	once := hasFlag("--once")
	loop := hasFlag("--loop")
	checkLink := flagValue("--check")

	appDir, err := findAppDir()
	if err != nil {
		fatal(err)
	}
	if err := os.Chdir(appDir); err != nil {
		fatal(err)
	}

	fmt.Println("========================================")
	fmt.Println(" P-Bandai HK - Setup & Launch")
	fmt.Println("========================================")
	fmt.Println("App folder:", appDir)

	py, err := ensurePython()
	if err != nil {
		fatal(err)
	}
	fmt.Println("Python:", py)

	if err := ensureVenv(py); err != nil {
		fatal(err)
	}
	venvPy := venvPythonPath()
	fmt.Println("Venv Python:", venvPy)

	if err := ensureDeps(venvPy); err != nil {
		fatal(err)
	}
	if err := ensureEnvFile(); err != nil {
		fatal(err)
	}

	fmt.Println("Setup complete.")
	if setupOnly {
		pauseIfWindows()
		return
	}

	if checkLink != "" {
		os.Exit(runBot(venvPy, "check", checkLink))
	}
	if once {
		os.Exit(runBot(venvPy, "once"))
	}
	if loop || oneClick {
		os.Exit(runBot(venvPy, "loop"))
	}

	// Interactive menu (double-click default)
	os.Exit(menu(venvPy))
}

func menu(venvPy string) int {
	in := bufio.NewReader(os.Stdin)
	for {
		fmt.Println()
		fmt.Println("What do you want to do?")
		fmt.Println("  [1] Start monitor loop (recommended)")
		fmt.Println("  [2] Run one scan")
		fmt.Println("  [3] Check a direct product link")
		fmt.Println("  [4] Open .env for editing")
		fmt.Println("  [5] Re-run setup (deps)")
		fmt.Println("  [6] List sessions")
		fmt.Println("  [7] Login / create session (multi + proxy)")
		fmt.Println("  [8] Login all task.csv (parallel + random proxies)")
		fmt.Println("  [Q] Quit")
		fmt.Print("> ")
		line, _ := in.ReadString('\n')
		choice := strings.TrimSpace(strings.ToLower(line))
		switch choice {
		case "1", "":
			return runBot(venvPy, "loop")
		case "2":
			return runBot(venvPy, "once")
		case "3":
			fmt.Print("Paste product URL or code: ")
			link, _ := in.ReadString('\n')
			link = strings.TrimSpace(link)
			if link == "" {
				fmt.Println("No link provided.")
				continue
			}
			code := runBot(venvPy, "check", link)
			if code != 0 {
				fmt.Println("Check failed. See logs/pbandai_hk.log")
			}
		case "4":
			if err := openEnvFile(); err != nil {
				fmt.Println("Could not open .env:", err)
				fmt.Println("Edit this file manually:", mustAbs(".env"))
			} else {
				fmt.Println("Opened .env — edit/save it, then continue here.")
			}
		case "5":
			if err := ensureDeps(venvPy); err != nil {
				fmt.Println("Setup error:", err)
			} else {
				fmt.Println("Dependencies reinstalled.")
			}
		case "6":
			_ = runBot(venvPy, "sessions")
		case "7":
			fmt.Print("Session name (e.g. acc1): ")
			name, _ := in.ReadString('\n')
			name = strings.TrimSpace(name)
			if name == "" {
				name = "acc1"
			}
			fmt.Print("Proxy URL (blank for none): ")
			proxy, _ := in.ReadString('\n')
			proxy = strings.TrimSpace(proxy)
			args := []string{"login", "--name", name, "--force"}
			if proxy != "" {
				args = append(args, "--proxy", proxy)
			}
			_ = runBot(venvPy, args...)
		case "8":
			createdCSV := false
			if copyCSVIfMissing("task.example.csv", "task.csv") {
				fmt.Println("Created task.csv from task.example.csv.")
				createdCSV = true
			}
			if copyCSVIfMissing("proxy.example.csv", "proxy.csv") {
				fmt.Println("Created proxy.csv from proxy.example.csv.")
				createdCSV = true
			}
			if createdCSV {
				fmt.Println("Edit task.csv (login/password) and proxy.csv, then choose [8] again.")
				fmt.Println("Proxy format: host:port:user:pass   OR   http://user:pass@host:port")
				continue
			}
			if _, err := os.Stat("task.csv"); err != nil {
				fmt.Println("Missing task.csv. Create it first.")
				continue
			}
			if _, err := os.Stat("proxy.csv"); err != nil {
				fmt.Println("Missing proxy.csv. Create it first.")
				continue
			}
			fmt.Print("Force re-login even if cookies exist? [y/N]: ")
			forceLine, _ := in.ReadString('\n')
			forceAns := strings.TrimSpace(strings.ToLower(forceLine))
			args := []string{"tasks"}
			if forceAns == "y" || forceAns == "yes" || forceAns == "1" {
				args = append(args, "--force")
			}
			_ = runBot(venvPy, args...)
		case "q", "quit", "exit":
			return 0
		default:
			fmt.Println("Unknown option.")
		}
	}
}

func findAppDir() (string, error) {
	exe, err := os.Executable()
	if err != nil {
		return "", err
	}
	exeDir := filepath.Dir(exe)

	candidates := []string{
		exeDir,
		filepath.Join(exeDir, ".."),
		mustAbs("."),
	}
	// When running `go run`, exe is in a temp dir; prefer cwd if it looks right.
	for _, dir := range candidates {
		if looksLikeAppDir(dir) {
			return filepath.Clean(dir), nil
		}
		nested := filepath.Join(dir, "pbandai-hk")
		if looksLikeAppDir(nested) {
			return filepath.Clean(nested), nil
		}
	}
	return "", fmt.Errorf("could not find app folder with web_shopping_bot_hk.py near %s", exeDir)
}

func looksLikeAppDir(dir string) bool {
	st, err := os.Stat(filepath.Join(dir, "web_shopping_bot_hk.py"))
	return err == nil && !st.IsDir()
}

func ensurePython() (string, error) {
	candidates := []string{}
	if runtime.GOOS == "windows" {
		candidates = append(candidates, "py", "python", "python3")
	} else {
		candidates = append(candidates, "python3", "python")
	}
	for _, c := range candidates {
		path, err := exec.LookPath(c)
		if err != nil {
			continue
		}
		// On Windows, `py` is the launcher.
		if c == "py" {
			if err := runCmd(path, "-3", "-c", "import sys; print(sys.version)"); err == nil {
				return path + "|py-3", nil
			}
			continue
		}
		if err := runCmd(path, "-c", "import sys; assert sys.version_info[:2] >= (3, 10)"); err == nil {
			return path, nil
		}
	}
	msg := "Python 3.10+ was not found.\nInstall Python from https://www.python.org/downloads/\n"
	if runtime.GOOS == "windows" {
		msg += "During install, enable \"Add python.exe to PATH\"."
	}
	return "", errors.New(msg)
}

func ensureVenv(py string) error {
	venvDir := ".venv"
	if _, err := os.Stat(venvPythonPath()); err == nil {
		return nil
	}
	fmt.Println("Creating virtual environment (.venv)...")
	args := []string{"-m", "venv", venvDir}
	bin, pyArgs := splitPy(py)
	return runCmd(bin, append(pyArgs, args...)...)
}

func ensureDeps(venvPy string) error {
	fmt.Println("Installing/updating dependencies...")
	if err := runCmd(venvPy, "-m", "pip", "install", "--upgrade", "pip"); err != nil {
		return err
	}
	return runCmd(venvPy, "-m", "pip", "install", "-r", "requirements.txt")
}

func ensureEnvFile() error {
	if _, err := os.Stat(".env"); err == nil {
		return ensureCSVTemplates()
	}
	fmt.Println("Creating .env from .env.example ...")
	data, err := os.ReadFile(".env.example")
	if err != nil {
		return err
	}
	if err := os.WriteFile(".env", data, 0o644); err != nil {
		return err
	}
	fmt.Println("Edit .env and set PRODUCT_LINKS before monitoring.")
	_ = openEnvFile()
	fmt.Println("Save .env, then continue in this window.")
	if runtime.GOOS == "windows" {
		fmt.Print("Press Enter after saving .env...")
		_, _ = bufio.NewReader(os.Stdin).ReadBytes('\n')
	} else {
		time.Sleep(500 * time.Millisecond)
	}
	return ensureCSVTemplates()
}

func ensureCSVTemplates() error {
	// Do not overwrite user task.csv / proxy.csv; only hint if missing.
	if _, err := os.Stat("task.csv"); err != nil {
		if _, err2 := os.Stat("task.example.csv"); err2 == nil {
			fmt.Println("Tip: copy task.example.csv -> task.csv and fill login/password rows.")
		}
	}
	if _, err := os.Stat("proxy.csv"); err != nil {
		if _, err2 := os.Stat("proxy.example.csv"); err2 == nil {
			fmt.Println("Tip: copy proxy.example.csv -> proxy.csv and fill proxy rows.")
		}
	}
	return nil
}

func copyCSVIfMissing(exampleName, destName string) bool {
	if _, err := os.Stat(destName); err == nil {
		return false
	}
	data, err := os.ReadFile(exampleName)
	if err != nil {
		return false
	}
	if err := os.WriteFile(destName, data, 0o644); err != nil {
		fmt.Println("Could not create", destName, ":", err)
		return false
	}
	return true
}

func openEnvFile() error {
	envPath, _ := filepath.Abs(".env")
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("cmd", "/c", "start", "", envPath)
	case "darwin":
		cmd = exec.Command("open", envPath)
	default:
		cmd = exec.Command("xdg-open", envPath)
	}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Start()
}

func runBot(venvPy string, args ...string) int {
	all := append([]string{"web_shopping_bot_hk.py"}, args...)
	cmd := exec.Command(venvPy, all...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	fmt.Println("Launching:", strings.Join(append([]string{venvPy}, all...), " "))
	if err := cmd.Run(); err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			fmt.Println("Bot exited with error. Check logs/pbandai_hk.log")
			pauseIfWindows()
			return ee.ExitCode()
		}
		fmt.Println("Launch failed:", err)
		pauseIfWindows()
		return 1
	}
	pauseIfWindows()
	return 0
}

func venvPythonPath() string {
	if runtime.GOOS == "windows" {
		return filepath.Join(".venv", "Scripts", "python.exe")
	}
	return filepath.Join(".venv", "bin", "python")
}

func splitPy(py string) (string, []string) {
	if strings.Contains(py, "|py-3") {
		return strings.Split(py, "|")[0], []string{"-3"}
	}
	return py, nil
}

func runCmd(name string, args ...string) error {
	if strings.Contains(name, "|py-3") {
		bin, pyArgs := splitPy(name)
		args = append(pyArgs, args...)
		name = bin
	}
	cmd := exec.Command(name, args...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func hasFlag(flag string) bool {
	for _, a := range os.Args[1:] {
		if a == flag {
			return true
		}
	}
	return false
}

func flagValue(flag string) string {
	for i, a := range os.Args[1:] {
		if a == flag && i+2 <= len(os.Args[1:]) {
			return os.Args[i+2]
		}
		prefix := flag + "="
		if strings.HasPrefix(a, prefix) {
			return strings.TrimPrefix(a, prefix)
		}
	}
	return ""
}

func mustAbs(p string) string {
	a, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	return a
}

func pauseIfWindows() {
	if runtime.GOOS != "windows" {
		return
	}
	// Keep console open after double-click.
	fmt.Print("\nPress Enter to close...")
	_, _ = bufio.NewReader(os.Stdin).ReadBytes('\n')
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "ERROR:", err)
	pauseIfWindows()
	os.Exit(1)
}
