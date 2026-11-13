const encoder = new TextEncoder();

async function verifySignature(secret, signature, body) {
    if (!signature || !/^[A-Za-z0-9+/]{43}=$/.test(signature)) {
        return false;
    }
    const key = await crypto.subtle.importKey(
        "raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"],
    );
    const bytes = Uint8Array.from(atob(signature), character => character.charCodeAt(0));
    return crypto.subtle.verify("HMAC", key, bytes, body);
}

export default {
    async fetch(request, env) {
        if (new URL(request.url).pathname !== "/line/webhook") {
            return new Response(null, { status: 404 });
        }
        if (request.method !== "POST") {
            return new Response(null, { status: 405, headers: { Allow: "POST" } });
        }
        const { LINE_CHANNEL_SECRET, GITHUB_DISPATCH_TOKEN, GITHUB_REPOSITORY } = env;
        if (!LINE_CHANNEL_SECRET || !GITHUB_DISPATCH_TOKEN || !GITHUB_REPOSITORY) {
            console.error("Missing Worker variables", {
                LINE_CHANNEL_SECRET: !LINE_CHANNEL_SECRET,
                GITHUB_DISPATCH_TOKEN: !GITHUB_DISPATCH_TOKEN,
                GITHUB_REPOSITORY: !GITHUB_REPOSITORY,
            });
            return new Response("Worker configuration incomplete", { status: 503 });
        }
        const body = await request.arrayBuffer();
        if (!await verifySignature(LINE_CHANNEL_SECRET, request.headers.get("x-line-signature"), body)) {
            console.error("LINE signature verification failed");
            return new Response(null, { status: 403 });
        }

        let events;
        try {
            ({ events } = JSON.parse(new TextDecoder().decode(body)));
            if (!Array.isArray(events)) {
                return new Response(null, { status: 400 });
            }
        } catch {
            return new Response(null, { status: 400 });
        }

        const requests = [];
        for (const event of events) {
            const message = event.message?.text;
            if (event.type !== "message" || event.mode === "standby"
                || event.message?.type !== "text" || typeof message !== "string"
                || !["查機票", "查機票*"].includes(message.trim().split(/\r?\n/, 1)[0].trim())) {
                continue;
            }
            const source = event.source;
            const target = { user: source?.userId, group: source?.groupId, room: source?.roomId }[source?.type];
            if (!target) {
                continue;
            }
            const endpoint = `https://api.github.com/repos/${GITHUB_REPOSITORY}/actions/workflows/LineFlightSearch.yml/dispatches`;
            requests.push(fetch(endpoint, {
                method: "POST",
                headers: {
                    Authorization: `Bearer ${GITHUB_DISPATCH_TOKEN}`,
                    Accept: "application/vnd.github+json",
                    "User-Agent": "line-flight-webhook",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                body: JSON.stringify({
                    ref: env.GITHUB_DISPATCH_REF || "main",
                    inputs: { target, message },
                }),
            }));
        }
        const results = await Promise.allSettled(requests);
        for (const result of results) {
            if (result.status === "rejected") {
                console.error("GitHub dispatch connection failed");
            } else if (!result.value.ok) {
                const details = await result.value.json().catch(() => null);
                const message = typeof details?.message === "string" ? details.message : "";
                const reason = /user.agent/i.test(message) ? "invalid user agent"
                    : /rate limit/i.test(message) ? "rate limit"
                    : /SAML|SSO/i.test(message) ? "organization SSO"
                    : /resource not accessible|permission/i.test(message) ? "token access or permissions"
                    : /disabled|policy/i.test(message) ? "GitHub policy or disabled resource"
                    : "unclassified";
                console.error("GitHub dispatch rejected", {
                    status: result.value.status,
                    requestId: result.value.headers.get("x-github-request-id"),
                    reason,
                    acceptedPermissions: result.value.headers.get("x-accepted-github-permissions"),
                });
            }
        }
        if (results.some(result => result.status === "rejected" || !result.value.ok)) {
            return new Response("GitHub Actions dispatch failed", { status: 503 });
        }
        return new Response(null, { status: 200 });
    },
};
