const els = {
  statusText: document.getElementById("statusText"),
  setupBtn: document.getElementById("setupBtn"),
  startBtn: document.getElementById("startBtn"),
  openBtn: document.getElementById("openBtn"),
  stopBtn: document.getElementById("stopBtn"),
  clearBtn: document.getElementById("clearBtn"),
  seedDemo: document.getElementById("seedDemo"),
  rebuildVenv: document.getElementById("rebuildVenv"),
  venvState: document.getElementById("venvState"),
  buildState: document.getElementById("buildState"),
  dbState: document.getElementById("dbState"),
  backendState: document.getElementById("backendState"),
  appUrl: document.getElementById("appUrl"),
  basePath: document.getElementById("basePath"),
  logs: document.getElementById("logs"),
};

function mark(value) {
  return value ? "جاهز" : "غير جاهز";
}

function setBusy(busy) {
  for (const button of [els.setupBtn, els.startBtn, els.openBtn, els.stopBtn]) {
    button.disabled = busy;
  }
}

function log(message) {
  els.logs.textContent += `${message}\n`;
  els.logs.scrollTop = els.logs.scrollHeight;
}

async function refreshStatus() {
  const status = await window.erpDesktop.getStatus();
  els.venvState.textContent = mark(status.hasVenv);
  els.buildState.textContent = mark(status.hasFrontendBuild);
  els.dbState.textContent = mark(status.hasDatabase);
  els.backendState.textContent = status.backendRunning ? "يعمل" : "متوقف";
  els.appUrl.textContent = status.appUrl;
  els.basePath.textContent = status.basePath;
  els.statusText.textContent = status.backendRunning ? "التطبيق يعمل" : "جاهز للأوامر";
}

async function runAction(label, fn) {
  setBusy(true);
  els.statusText.textContent = label;
  try {
    await fn();
    await refreshStatus();
  } catch (error) {
    log(`ERROR: ${error.message || error}`);
    els.statusText.textContent = "حدث خطأ";
  } finally {
    setBusy(false);
  }
}

els.setupBtn.addEventListener("click", () => {
  runAction("جار الإعداد...", () => window.erpDesktop.setup({
    seedDemo: els.seedDemo.checked,
    rebuildVenv: els.rebuildVenv.checked,
  }));
});

els.startBtn.addEventListener("click", () => {
  runAction("جار تشغيل الباك إند...", () => window.erpDesktop.start());
});

els.openBtn.addEventListener("click", () => {
  runAction("جار فتح التطبيق...", () => window.erpDesktop.openApp());
});

els.stopBtn.addEventListener("click", () => {
  runAction("جار الإيقاف...", () => window.erpDesktop.stop());
});

els.clearBtn.addEventListener("click", () => {
  els.logs.textContent = "";
});

window.erpDesktop.onLog(log);
refreshStatus();
