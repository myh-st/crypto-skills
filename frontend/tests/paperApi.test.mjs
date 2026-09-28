import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { createPaperApi } from "../modules/paperApi.js";

function response(payload, { status = 200, contentType = "application/json" } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ "Content-Type": contentType }),
    json: async () => payload,
    blob: async () => new Blob(["paper-export"]),
  };
}

test("paper API sends same-origin JSON and never persists provider settings in browser state", async () => {
  const calls = [];
  const api = createPaperApi(async (url, options) => {
    calls.push({ url, options });
    return response({ provider: { credential_status: "credential_reference_configured" } });
  });

  const provider = {
    provider_id: "local-jev",
    kind: "typesafe_jev",
    display_name: "Jev",
    model: "jev-latest",
    credential_env: "TYPESAFE_API_KEY",
  };
  const saved = await api.saveProvider(provider);

  assert.equal(saved.provider.credential_status, "credential_reference_configured");
  assert.equal(calls[0].url, "/api/providers");
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.headers.get("Content-Type"), "application/json");
  assert.equal(JSON.parse(calls[0].options.body).credential_env, "TYPESAFE_API_KEY");
  const source = readFileSync(new URL("../modules/paperApi.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /localStorage|sessionStorage/);
});

test("paper API exports a binary bundle without copying it into persistent state", async () => {
  const calls = [];
  const api = createPaperApi(async (url, options) => {
    calls.push({ url, options });
    return response(null, { contentType: "application/zip" });
  });
  const bundle = await api.downloadExport();

  assert.equal(calls[0].url, "/api/export");
  assert.equal(bundle.size, 12);
  assert.equal(calls[0].options.cache, "no-store");
});

test("paper API exposes bounded server errors without echoing request content", async () => {
  const api = createPaperApi(async () => response({ error: "provider credential reference is unset" }, { status: 400 }));
  await assert.rejects(
    api.testProvider("local-jev"),
    /provider credential reference is unset/,
  );
});
