const { app, BrowserWindow, ipcMain, shell } = require("electron");
const path = require("node:path");
const fs = require("node:fs");
const { spawn } = require("node:child_process");
const http = require("node:http");

const ROOT = path.resolve(__dirname, "..");
const BACKEND_DIR = path.join(ROOT, "backend");
const FRONTEND_DIR = path.join(ROOT, "react-src");
const DIST_DIR = path.join(FRONTEND_DIR, "dist");
const LEGACY_FRONTEND_DIR = path.join(ROOT, "frontend");
const APP_URL = "http://127.0.0.1:8000";

let setupWindow;
let appWindow;
let backendProcess;

function userPaths() {
  const base = app.getPath("userData");
  return {
    base,
    runtime: path.join(base, "runtime"),
    venv: path.join(base, "runtime", ".venv"),
    data: path.join(base, "data"),
    uploads: path.join(base, "uploads"),
  };
}

function pythonPath() {
  const { venv } = userPaths();
  if (process.platform === "win32") return path.join(venv, "Scripts", "python.exe");
  return path.join(venv, "bin", "python");
}

function emitLog(message) {
  const line = `[${new Date().toLocaleTimeString()}] ${message}`;
  for (const win of [setupWindow, appWindow]) {
    if (win && !win.isDestroyed()) win.webContents.send("desktop:log", line);
  }
}

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    emitLog(`> ${command} ${args.join(" ")}`);
    const child = spawn(command, args, {
      cwd: options.cwd || ROOT,
      env: { ...process.env, ...(options.env || {}) },
      shell: process.platform === "win32",
    });

    child.stdout.on("data", (data) => emitLog(data.toString().trim()));
    child.stderr.on("data", (data) => emitLog(data.toString().trim()));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} exited with code ${code}`));
    });
  });
}

function firstExisting(paths) {
  return paths.find((item) => fs.existsSync(item));
}

async function findPython() {
  if (process.platform === "win32") {
    try {
      await run("py", ["-3", "--version"]);
      return { command: "py", args: ["-3"] };
    } catch (_) {
      return { command: "python", args: [] };
    }
  }
  return { command: "python3", args: [] };
}

async function hasNpm() {
  try {
    await run("npm", ["--version"]);
    return true;
  } catch (_) {
    return false;
  }
}

function desktopEnv() {
  const paths = userPaths();
  const staticDir = fs.existsSync(path.join(DIST_DIR, "index.html")) ? DIST_DIR : LEGACY_FRONTEND_DIR;
  fs.mkdirSync(paths.data, { recursive: true });
  fs.mkdirSync(paths.uploads, { recursive: true });
  return {
    DATABASE_URL: `sqlite+aiosqlite:///${path.join(paths.data, "heavy_erp.db").replace(/\\/g, "/")}`,
    ERP_UPLOAD_DIR: paths.uploads,
    ERP_STATIC_DIR: staticDir,
    JWT_SECRET: "heavy-erp-desktop-local-secret",
    ENVIRONMENT: "desktop",
    PYTHONPATH: BACKEND_DIR,
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
  };
}

async function waitForHealth(timeoutMs = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const ok = await new Promise((resolve) => {
      const req = http.get(`${APP_URL}/health`, (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      });
      req.on("error", () => resolve(false));
      req.setTimeout(1000, () => {
        req.destroy();
        resolve(false);
      });
    });
    if (ok) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

async function setupDesktop(options = {}) {
  const paths = userPaths();
  fs.mkdirSync(paths.runtime, { recursive: true });
  fs.mkdirSync(paths.data, { recursive: true });
  fs.mkdirSync(paths.uploads, { recursive: true });

  const py = await findPython();
  const venvPython = pythonPath();
  if (options.rebuildVenv && fs.existsSync(paths.venv)) {
    emitLog("Removing old Python environment...");
    fs.rmSync(paths.venv, { recursive: true, force: true });
  }
  if (!fs.existsSync(venvPython)) {
    await run(py.command, [...py.args, "-m", "venv", paths.venv]);
  }

  await run(venvPython, ["-m", "pip", "install", "--upgrade", "pip"]);
  await run(venvPython, ["-m", "pip", "install", "-r", path.join(BACKEND_DIR, "requirements-desktop.txt")]);

  if (await hasNpm()) {
    emitLog("Installing frontend dependencies...");
    await run("npm", ["ci"], { cwd: FRONTEND_DIR });
    emitLog("Building frontend...");
    await run("npm", ["run", "build"], { cwd: FRONTEND_DIR });
  } else if (fs.existsSync(path.join(LEGACY_FRONTEND_DIR, "index.html"))) {
    emitLog("npm was not found. Using the bundled frontend build.");
  } else {
    throw new Error("npm is required to build the frontend, and no bundled frontend build was found.");
  }

  const initArgs = ["-m", "app.desktop"];
  if (options.seedDemo) initArgs.push("--seed-demo");
  await run(venvPython, initArgs, { cwd: BACKEND_DIR, env: desktopEnv() });

  emitLog("Setup finished.");
  return getStatus();
}

async function startBackend() {
  if (backendProcess && !backendProcess.killed) return true;
  const venvPython = pythonPath();
  if (!fs.existsSync(venvPython)) {
    throw new Error("Setup is required before starting the app.");
  }
  if (!fs.existsSync(path.join(DIST_DIR, "index.html"))) {
    throw new Error("Frontend build is missing. Run setup first.");
  }

  backendProcess = spawn(
    venvPython,
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
    {
      cwd: BACKEND_DIR,
      env: { ...process.env, ...desktopEnv() },
      shell: process.platform === "win32",
    }
  );
  backendProcess.stdout.on("data", (data) => emitLog(data.toString().trim()));
  backendProcess.stderr.on("data", (data) => emitLog(data.toString().trim()));
  backendProcess.on("close", (code) => {
    emitLog(`Backend stopped with code ${code}`);
    backendProcess = undefined;
  });

  const healthy = await waitForHealth();
  if (!healthy) throw new Error("Backend did not become healthy on port 8000.");
  return true;
}

function stopBackend() {
  if (backendProcess && !backendProcess.killed) backendProcess.kill();
  backendProcess = undefined;
  return true;
}

function getStatus() {
  const paths = userPaths();
  return {
    basePath: paths.base,
    hasVenv: fs.existsSync(pythonPath()),
    hasFrontendBuild: fs.existsSync(path.join(DIST_DIR, "index.html")) || fs.existsSync(path.join(LEGACY_FRONTEND_DIR, "index.html")),
    hasDatabase: fs.existsSync(path.join(paths.data, "heavy_erp.db")),
    backendRunning: Boolean(backendProcess && !backendProcess.killed),
    appUrl: APP_URL,
  };
}

function createSetupWindow() {
  setupWindow = new BrowserWindow({
    width: 980,
    height: 720,
    minWidth: 860,
    minHeight: 640,
    title: "Heavy ERP Desktop",
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  setupWindow.loadFile(path.join(__dirname, "setup.html"));
}

async function openAppWindow() {
  await startBackend();
  if (!appWindow || appWindow.isDestroyed()) {
    appWindow = new BrowserWindow({
      width: 1280,
      height: 820,
      title: "Heavy ERP",
      webPreferences: {
        preload: path.join(__dirname, "preload.cjs"),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
  }
  await appWindow.loadURL(APP_URL);
  appWindow.show();
  return true;
}

ipcMain.handle("desktop:get-status", () => getStatus());
ipcMain.handle("desktop:setup", (_event, options) => setupDesktop(options));
ipcMain.handle("desktop:start", () => startBackend());
ipcMain.handle("desktop:stop", () => stopBackend());
ipcMain.handle("desktop:open-app", () => openAppWindow());

app.whenReady().then(createSetupWindow);
app.on("before-quit", stopBackend);
app.on("window-all-closed", () => {
  stopBackend();
  if (process.platform !== "darwin") app.quit();
});
