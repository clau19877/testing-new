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
		fmt.Println("  [1] Start click farm / monitor loop (recommended)")
		fmt.Println("  [2] Run one pass")
		fmt.Println("  [3] Check a direct product link")
		fmt.Println("  [4] Open .env for editing")
		fmt.Println("  [0] Reset .env from latest .env.example (backup old)")
		fmt.Println("  [5] Re-run setup (deps)")
		fmt.Println("  [6] List sessions (legacy)")
		fmt.Println("  [9] Diagnose product cart eligibility (detailed log)")
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
		case "0":
			fmt.Print("Replace .env with .env.example? [y/N]: ")
			ans, _ := in.ReadString('\n')
			ans = strings.TrimSpace(strings.ToLower(ans))
			if ans == "y" || ans == "yes" || ans == "1" {
				if err := resetEnvFromExample(); err != nil {
					fmt.Println("Reset .env failed:", err)
				}
			}
		case "5":
			if err := ensureDeps(venvPy); err != nil {
				fmt.Println("Setup error:", err)
			} else {
				fmt.Println("Dependencies reinstalled.")
			}
		case "6":
			_ = runBot(venvPy, "sessions")
		case "7", "8":
			fmt.Println("Login removed. Guest click farm needs no accounts.")
			fmt.Println("Set CLICK_FARM=1, BROWSER_INSTANCES, DISCORD_WEBHOOK_URL in .env")
		case "9":
			fmt.Print("Paste product URL or code to diagnose: ")
			link, _ := in.ReadString('\n')
			link = strings.TrimSpace(link)
			if link == "" {
				fmt.Println("No link provided.")
				continue
			}
			_ = runBot(venvPy, "diagnose", link)
			fmt.Println("See logs/pbandai_hk.log and logs/diagnostics/")
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
		if n, err := mergeMissingEnvKeys(".env.example", ".env"); err != nil {
			fmt.Println("WARNING: could not merge new .env keys:", err)
		} else if n > 0 {
			fmt.Printf("Updated .env with %d new key(s) from .env.example\n", n)
			fmt.Println("Review .env — new drop settings may need values.")
		}
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

func resetEnvFromExample() error {
	data, err := os.ReadFile(".env.example")
	if err != nil {
		return err
	}
	if _, err := os.Stat(".env"); err == nil {
		bak := fmt.Sprintf(".env.bak.%d", time.Now().Unix())
		old, err := os.ReadFile(".env")
		if err != nil {
			return err
		}
		if err := os.WriteFile(bak, old, 0o644); err != nil {
			return err
		}
		fmt.Println("Backed up old .env ->", bak)
	}
	if err := os.WriteFile(".env", data, 0o644); err != nil {
		return err
	}
	fmt.Println("Replaced .env with latest .env.example")
	return openEnvFile()
}

func mergeMissingEnvKeys(examplePath, envPath string) (int, error) {
	exampleData, err := os.ReadFile(examplePath)
	if err != nil {
		return 0, err
	}
	envData, err := os.ReadFile(envPath)
	if err != nil {
		return 0, err
	}
	existing := envKeySet(string(envData))
	var toAppend []string
	for _, raw := range strings.Split(string(exampleData), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		key := strings.TrimSpace(strings.SplitN(line, "=", 2)[0])
		if key == "" {
			continue
		}
		if _, ok := existing[key]; ok {
			continue
		}
		toAppend = append(toAppend, strings.TrimRight(raw, "\r\n"))
		existing[key] = struct{}{}
	}
	if len(toAppend) == 0 {
		return 0, nil
	}
	f, err := os.OpenFile(envPath, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	if _, err := f.WriteString("\n# --- keys added from .env.example ---\n"); err != nil {
		return 0, err
	}
	if _, err := f.WriteString(strings.Join(toAppend, "\n") + "\n"); err != nil {
		return 0, err
	}
	return len(toAppend), nil
}

func envKeySet(text string) map[string]struct{} {
	out := map[string]struct{}{}
	for _, raw := range strings.Split(text, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		key := strings.TrimSpace(strings.SplitN(line, "=", 2)[0])
		if key != "" {
			out[key] = struct{}{}
		}
	}
	return out
}

func ensureCSVTemplates() error {
	// Do not overwrite user proxy.csv; only hint if missing.
	if _, err := os.Stat("proxy.csv"); err != nil {
		if _, err2 := os.Stat("proxy.example.csv"); err2 == nil {
			fmt.Println("Tip: copy proxy.example.csv -> proxy.csv for click-farm proxies.")
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
