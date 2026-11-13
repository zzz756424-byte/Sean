import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { webcrypto } from "node:crypto";
import { test } from "node:test";

const code = readFileSync(new URL("../line-webhook-worker.js", import.meta.url));
const { default: worker } = await import(`data:text/javascript;base64,${code.toString("base64")}`);
const env = {
    LINE_CHANNEL_SECRET: "test-secret",
    GITHUB_DISPATCH_TOKEN: "test-token",
    GITHUB_REPOSITORY: "example/test",
};
const message = "查機票*\n去程地點:台中\n回程地點:沖繩\n最早去程時間:2027/5/8\n最晚回程時間:2027/5/15\n最短遊玩天數:7";

async function send(events) {
    const body = JSON.stringify({ events });
    const key = await webcrypto.subtle.importKey("raw", new TextEncoder().encode(env.LINE_CHANNEL_SECRET),
        { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const signature = Buffer.from(await webcrypto.subtle.sign("HMAC", key, new TextEncoder().encode(body))).toString("base64");
    return worker.fetch(new Request("https://example.test/line/webhook", {
        method: "POST", body, headers: { "x-line-signature": signature },
    }), env);
}

function event(text, mode = "active") {
    return { type: "message", mode, message: { type: "text", text }, source: { type: "user", userId: "test-user" } };
}

test("dispatches original and full-width commands with normalized inputs", async t => {
    const calls = [];
    t.mock.method(globalThis, "fetch", async (url, options) => {
        calls.push({ url, body: JSON.parse(options.body) });
        return new Response(null, { status: 204 });
    });
    for (const text of [message, message.replace("*", "＊").replaceAll("\n", "\r\n")]) {
        assert.equal((await send([event(text)])).status, 200);
        assert.equal(calls.at(-1).body.inputs.message, message);
        assert.equal(calls.at(-1).body.ref, "main");
        assert.match(calls.at(-1).url, /LineFlightSearch.yml\/dispatches$/);
    }
    assert.equal(calls.length, 2);
});

test("logs skipped events and empty verification requests without dispatching", async t => {
    const logs = [];
    t.mock.method(console, "info", (...args) => logs.push(args));
    const fetchMock = t.mock.method(globalThis, "fetch", () => { throw new Error("unexpected dispatch"); });
    assert.equal((await send([])).status, 200);
    assert.equal((await send([event("hello"), event(message, "standby")])).status, 200);
    assert.equal(fetchMock.mock.callCount(), 0);
    assert.ok(logs.some(([, data]) => data.eventCount === 0));
    assert.ok(logs.some(([, data]) => data.reason === "unsupported_command"));
    assert.ok(logs.some(([, data]) => data.reason === "standby"));
});

test("GitHub rejection returns 503 and records permission failure", async t => {
    const logs = [];
    t.mock.method(console, "error", (...args) => logs.push(args));
    t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({ message: "Resource not accessible" }), { status: 403 }));
    assert.equal((await send([event(message)])).status, 503);
    assert.ok(logs.some(([, data]) => data.status === 403 && data.reason === "token access or permissions"));
});
