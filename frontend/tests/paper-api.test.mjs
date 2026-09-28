import test from "node:test";
import assert from "node:assert/strict";

import { createPaperApi } from "../modules/paperApi.js";

function response(payload = {}, { status = 200, archive } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    blob: async () => archive ?? new Blob(["zip fixture"], { type: "application/zip" }),
  };
}

function createRecordingApi(responseFactory = () => response({ ok: true })) {
  const calls = [];
  const api = createPaperApi(async (url, options) => {
    calls.push({ url, options });
    return responseFactory(url, options);
  });
  return { api, calls };
}

function assertRequestPolicy(calls) {
  for (const { options } of calls) {
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.cache, "no-store");
  }
}

function assertJsonPost(call, expectedUrl, expectedBody) {
  assert.equal(call.url, expectedUrl);
  assert.equal(call.options.method, "POST");
  assert.equal(call.options.headers.get("Content-Type"), "application/json");
  assert.equal(typeof call.options.body, "string");
  assert.deepEqual(JSON.parse(call.options.body), expectedBody);
}

test("public methods use the expected same-origin, no-store endpoints", async () => {
  const archive = new Blob(["PK\u0003\u0004fixture"], { type: "application/zip" });
  const { api, calls } = createRecordingApi(() => response({ ok: true }, { archive }));

  await api.health();
  await api.experiment();
  await api.saveExperiment({ symbol: "BTC/USDT" });
  await api.providers();
  await api.saveProvider({ provider_id: "provider-1" });
  await api.testProvider("provider-1");
  await api.dashboard();
  await api.evaluation();
  await api.runtime("start");
  await api.runCycle();
  await api.testMarketData("synthetic");
  await api.reducePosition("position-1");
  const downloaded = await api.downloadExport();

  assert.deepEqual(
    calls.map(({ url }) => url),
    [
      "/api/health",
      "/api/experiment",
      "/api/experiment",
      "/api/providers",
      "/api/providers",
      "/api/providers/provider-1/test",
      "/api/dashboard",
      "/api/evaluation",
      "/api/runtime/start",
      "/api/runtime/cycle",
      "/api/market-data/test",
      "/api/positions/position-1/reduce",
      "/api/export",
    ],
  );
  assert.deepEqual(
    calls.map(({ options }) => options.method ?? "GET"),
    [
      "GET",
      "GET",
      "POST",
      "GET",
      "POST",
      "POST",
      "GET",
      "GET",
      "POST",
      "POST",
      "POST",
      "POST",
      "GET",
    ],
  );
  assertRequestPolicy(calls);
  assert.equal(downloaded, archive);
  assert.ok(downloaded instanceof Blob);
  assert.equal(downloaded.type, "application/zip");
  assert.equal(await downloaded.text(), "PK\u0003\u0004fixture");
});

test("JSON requests serialize their payloads and encode provider and position IDs", async () => {
  const { api, calls } = createRecordingApi();
  const experiment = { symbol: "BTC/USDT", limits: { maxPositions: 2 } };
  const provider = { provider_id: "provider-1", model: "fixture-model" };

  await api.saveExperiment(experiment);
  await api.saveProvider(provider);
  await api.testProvider("provider /one?");
  await api.runCycle();
  await api.runCycle("BTC/USDT");
  await api.testMarketData("synthetic");
  await api.reducePosition("position /one#", 0.25);

  assertJsonPost(calls[0], "/api/experiment", experiment);
  assertJsonPost(calls[1], "/api/providers", provider);
  assertJsonPost(calls[2], "/api/providers/provider%20%2Fone%3F/test", {});
  assertJsonPost(calls[3], "/api/runtime/cycle", { manual: true });
  assertJsonPost(calls[4], "/api/runtime/cycle", { symbol: "BTC/USDT", manual: true });
  assertJsonPost(calls[5], "/api/market-data/test", { mode: "synthetic" });
  assertJsonPost(calls[6], "/api/positions/position%20%2Fone%23/reduce", { fraction: 0.25 });
  assertRequestPolicy(calls);
});

test("runtime accepts only the supported actions", async () => {
  const { api, calls } = createRecordingApi();
  const actions = ["start", "pause", "resume", "stop"];

  for (const action of actions) {
    await api.runtime(action);
  }

  assert.deepEqual(
    calls.map(({ url }) => url),
    actions.map((action) => `/api/runtime/${action}`),
  );
  for (const call of calls) {
    assertJsonPost(call, call.url, {});
  }
  assertRequestPolicy(calls);

  const requestCount = calls.length;
  await assert.rejects(api.runtime("restart"), /Unsupported runtime action/);
  assert.equal(calls.length, requestCount);
});

test("provider failures expose only the bounded error message, not response secrets", async () => {
  const responseSecret = "DUMMY_SECRET_MUST_NOT_ESCAPE";
  const { api, calls } = createRecordingApi(() =>
    response(
      {
        error: "Provider test failed",
        credential: responseSecret,
        debug: `Authorization: Bearer ${responseSecret}`,
      },
      { status: 502 },
    ),
  );

  await assert.rejects(api.testProvider("provider-1"), (error) => {
    assert.equal(error.message, "Provider test failed");
    assert.ok(error.message.length <= 64);
    assert.ok(!error.message.includes(responseSecret));
    return true;
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/providers/provider-1/test");
  assertRequestPolicy(calls);
});

test("provider failures with unreadable bodies return a bounded status message", async () => {
  const responseSecret = "DUMMY_SECRET_MUST_NOT_ESCAPE";
  const { api, calls } = createRecordingApi(() => ({
    ok: false,
    status: 503,
    json: async () => {
      throw new Error(`untrusted response details: ${responseSecret}`);
    },
  }));

  await assert.rejects(api.testProvider("provider-1"), (error) => {
    assert.equal(error.message, "Request failed (503)");
    assert.ok(error.message.length <= 64);
    assert.ok(!error.message.includes(responseSecret));
    return true;
  });
  assert.equal(calls.length, 1);
  assertRequestPolicy(calls);
});
