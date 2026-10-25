Idempotency-Key = sha256 of the request body, so retries and resumed runs reuse it.
Breaks billing/server_api.md "Idempotency": "two different charges never share a key — even when the customer, the amount and the memo are identical".
