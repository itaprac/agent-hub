// Local view controllers. Reducers are pure so transient interaction state can
// be tested without a DOM, while each mounted view owns its controller instance.

export const initialUsageViewState = Object.freeze({
  days: 30,
  metric: "cost",
  breakdown: "model",
  requestId: 0,
  loading: false,
  error: null,
});

export function reduceUsageView(state, action) {
  switch (action.type) {
    case "select-days": {
      const days = Number(action.days);
      if (!Number.isInteger(days) || days <= 0 || days === state.days) return state;
      return { ...state, days };
    }
    case "select-metric":
      if (!["cost", "tokens"].includes(action.metric) || action.metric === state.metric) return state;
      return { ...state, metric: action.metric };
    case "select-breakdown":
      if (!["model", "time"].includes(action.breakdown) || action.breakdown === state.breakdown) return state;
      return { ...state, breakdown: action.breakdown };
    case "request-started":
      return { ...state, requestId: state.requestId + 1, loading: true, error: null };
    case "request-finished":
      if (action.requestId !== state.requestId) return state;
      return { ...state, loading: false, error: null };
    case "request-failed":
      if (action.requestId !== state.requestId) return state;
      return { ...state, loading: false, error: action.error || "Usage request failed" };
    default:
      return state;
  }
}

export const initialSettingsViewState = Object.freeze({ tokenDraft: "" });

export function reduceSettingsView(state, action) {
  switch (action.type) {
    case "edit-token": {
      const tokenDraft = String(action.value ?? "");
      return tokenDraft === state.tokenDraft ? state : { ...state, tokenDraft };
    }
    case "save-finished":
      return state.tokenDraft ? { ...state, tokenDraft: "" } : state;
    default:
      return state;
  }
}

export const initialPeersViewState = Object.freeze({ dryRun: false, running: null });

export function reducePeersView(state, action) {
  switch (action.type) {
    case "set-dry-run": {
      if (state.running) return state;
      const dryRun = Boolean(action.value);
      return dryRun === state.dryRun ? state : { ...state, dryRun };
    }
    case "command-started":
      if (state.running || !action.machine || !["apply", "sync"].includes(action.command)) return state;
      return {
        ...state,
        running: {
          machine: action.machine,
          command: action.command,
          dryRun: state.dryRun,
        },
      };
    case "command-finished":
      return state.running ? { ...state, running: null } : state;
    default:
      return state;
  }
}

export function projectPeersView(state, { busy = 0, loading = false } = {}) {
  return {
    dryRun: state.dryRun,
    running: state.running,
    controlsDisabled: Number(busy) > 0 || Boolean(state.running) || Boolean(loading),
  };
}

export function createLocalController(initialState, reducer, onChange = () => {}) {
  let state = initialState;
  return {
    get state() {
      return state;
    },
    dispatch(action) {
      const next = reducer(state, action);
      if (next === state) return false;
      state = next;
      onChange(state);
      return true;
    },
  };
}
