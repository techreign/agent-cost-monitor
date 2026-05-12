# agent-cost-monitor

**Self-hosted AI agent cost monitor with cross-provider runaway detection.**

Track every LLM API call across Anthropic, OpenAI, Gemini, and Groq.
Spot runaway agents before they drain your budget. No cloud required.

---

## Why this exists

From r/LocalLLaMA (May 2026, 181 upvotes):

> "What in tarnation is going on with the cost of compute. I left an agent
> running overnight and woke up to a $47 bill. I had no idea it was looping."

LangSmith and Helicone solve this -- for cloud workloads, at cloud prices.
If you self-host your models, run air-gapped, or just want your cost data
on your own machine, those tools do not fit. This does.

---

## 30-second install

```bash
pip install -r requirements.txt
uvicorn app:app --reload
# Open http://localhost:8000
```

Set `COST_DB_PATH` to a writable path if the default `./costs.db` does
not suit your deployment.

---

## Integration (Anthropic SDK)

Drop this wrapper around any `client.messages.create` call:

```python
import httpx, time

def tracked_create(client, monitor_url="http://localhost:8000", **kwargs):
    resp = client.messages.create(**kwargs)
    usage = resp.usage
    httpx.post(f"{monitor_url}/ingest", json={
        "api": "anthropic",
        "model": kwargs["model"],
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "project_id": kwargs.get("metadata", {}).get("project_id", "default"),
        "agent_id": kwargs.get("metadata", {}).get("agent_id"),
    })
    return resp
```

The same pattern works for OpenAI (`choices[0].usage`) and Gemini
(`result.usage_metadata`). See the `/ingest` schema in `app.py`.

---

## Dashboard

```
+--[ AI Agent Cost Monitor ]-----------------------------------------+
| Today's Spend   Week to Date   Total (30d)   Runaway Agents        |
|   $0.003412       $0.21483       $1.84291         0                |
+--------------------------------------------------------------------+
| Cost Burn (Hourly, last 48h)  [bar chart]                          |
+--------------------------------------------------------------------+
| Top Agents (Last 24h)                                              |
|  Agent ID          Requests   Cost (USD)   Status                  |
|  code-gen-worker   142        $0.019201    OK                      |
|  data-fetch-loop   891        $0.217400    RUNAWAY                 |
+--------------------------------------------------------------------+
| Top Models by Burn                                                  |
|  claude-sonnet-4-6   anthropic   $0.941002                         |
|  gpt-4o              openai      $0.821334                         |
+--------------------------------------------------------------------+
```

---

## Runaway detection

An agent is flagged as **runaway** when its cost in the last hour
exceeds 3x the median hourly rate across all agents in the same project
over the previous 24 hours. The threshold is intentionally conservative:
a transient spike does not trigger it; a stuck loop does.

---

## Comparison

| Tool        | Self-hosted | Free tier         | Price (cloud)     |
|-------------|-------------|-------------------|-------------------|
| **This**    | Yes         | Unlimited (OSS)   | Free              |
| LangSmith   | No          | Limited (traces)  | $99/mo            |
| Helicone    | No          | 10k req/mo        | $99+/mo           |
| Portkey      | No          | Rate-limited      | $19/mo            |

---

## API

| Method | Path           | Description                              |
|--------|----------------|------------------------------------------|
| POST   | `/ingest`      | Record a single LLM call + cost          |
| GET    | `/api/summary` | Aggregated cost + runaway detection      |
| GET    | `/`            | Dashboard HTML                           |

`POST /ingest` accepts `api`, `model`, `input_tokens`, `output_tokens`,
`project_id`, and optional `agent_id`, `request_id`, `cost_usd`.
If `cost_usd` is omitted, it is computed from the built-in pricing table.

---

## Supported models (built-in pricing)

Anthropic: claude-sonnet-4-6, claude-opus-4-7, claude-haiku-4-5
OpenAI: gpt-5, gpt-5-mini, gpt-4o, gpt-4o-mini, o3, o3-mini
Gemini: gemini-2.5-pro, gemini-2.5-flash, gemini-2.0-flash
Groq: llama-3.3-70b, llama-3.1-8b, mixtral-8x7b

Pass `cost_usd` explicitly for any model not in this list.

---

## Docker

```bash
docker build -t agent-cost-monitor .
docker run -p 8080:8080 -v $(pwd)/data:/data \
  -e COST_DB_PATH=/data/costs.db \
  agent-cost-monitor
```

---

## Tests

```bash
pytest tests/ -v
```

9 tests, all passing.

---

## License

MIT. See [LICENSE](LICENSE).

---

Built by [manifold.digital](https://manifold-storefront-kqnamqi9a-manifolds-projects-9c6cc827.vercel.app). The demand signal catalog that sourced this idea is available for $1 at https://buy.stripe.com/7sYfZh2h38IBbFvfrf5wI00.
