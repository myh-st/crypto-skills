// Central, dependency-free application store. Persists browser-local demo
// state (runs, decisions, settings) to localStorage so the workflow survives
// a page reload, but never talks to a network API.

const STORAGE_KEY = "crypto-research-console.v1";

const listeners = new Set();

function loadPersisted() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (error) {
    console.warn("Could not read persisted demo state:", error);
    return null;
  }
}

function persist(state) {
  try {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        runs: state.runs,
        decisions: state.decisions,
        settings: state.settings,
      }),
    );
  } catch (error) {
    console.warn("Could not persist demo state:", error);
  }
}

function linkWatchlistToRuns(watchlist, runs) {
  return watchlist.map((entry) => {
    const latestRun = runs.find((run) => run.asset === entry.asset);
    return latestRun ? { ...entry, linkedRunId: latestRun.id } : { ...entry };
  });
}

export function createStore(seed) {
  const persisted = loadPersisted();
  const runs = persisted?.runs?.length ? persisted.runs : seed.runs;
  const state = {
    runs,
    decisions: persisted?.decisions?.length ? persisted.decisions : seed.decisions,
    settings: { ...seed.settings, ...(persisted?.settings || {}) },
    watchlist: linkWatchlistToRuns(seed.watchlist, runs),
    dataSources: seed.dataSources,
    evaluationDemo: seed.evaluationDemo,
  };

  function notify() {
    persist(state);
    listeners.forEach((listener) => listener(state));
  }

  return {
    getState() {
      return state;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    addRun(run) {
      state.runs = [run, ...state.runs];
      state.watchlist = state.watchlist.map((entry) => (
        entry.asset === run.asset ? { ...entry, linkedRunId: run.id } : entry
      ));
      notify();
    },
    updateRun(runId, patch) {
      state.runs = state.runs.map((run) => (run.id === runId ? { ...run, ...patch } : run));
      notify();
    },
    addDecision(decision) {
      state.decisions = [decision, ...state.decisions];
      notify();
    },
    updateSettings(patch) {
      state.settings = { ...state.settings, ...patch };
      notify();
    },
    resetDemoData(seedData) {
      state.runs = seedData.runs;
      state.decisions = seedData.decisions;
      state.watchlist = linkWatchlistToRuns(seedData.watchlist, seedData.runs);
      notify();
    },
  };
}
