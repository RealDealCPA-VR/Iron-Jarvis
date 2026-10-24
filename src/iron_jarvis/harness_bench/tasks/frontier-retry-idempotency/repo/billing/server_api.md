# Payments server API (v2)

The payments server makes card charges. `billing.PaymentsClient` speaks this
API through a `Transport` (`billing/transport.py`). The tests use
`FakeTransport`, which behaves exactly like the real server, network faults
included.

## POST /charges

Request body (JSON):

| field          | type    | notes                               |
| -------------- | ------- | ----------------------------------- |
| `customer`     | string  | the customer id, e.g. `cus_1001`    |
| `amount_cents` | integer | greater than 0                      |
| `memo`         | string  | shown on the customer's statement   |

Request headers:

| header            | notes                                   |
| ----------------- | --------------------------------------- |
| `Idempotency-Key` | send one with every charge; see below   |
| `User-Agent`      | optional                                |

Answers:

| status | body                                                                        | meaning                                                                                  |
| ------ | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| 200    | `{"id": "ch_...", "status": "succeeded", "customer", "amount_cents", "memo"}` | the charge was made                                                                      |
| 400    | `{"error": "invalid_request", "message": ...}`                              | the request is wrong (bad amount, no customer); nothing was charged                      |
| 402    | `{"error": "card_declined", "message": ...}`                                | the card was declined; nothing was charged                                               |
| 409    | `{"error": "idempotency_conflict", "message": ...}`                         | the Idempotency-Key was already used for a DIFFERENT request; nothing was charged        |
| 503    | `{"error": "busy", "message": ...}`                                         | the server is overloaded and did NOT process the request; nothing was charged            |

## Idempotency

Send an `Idempotency-Key` header with every charge. A repeated request with the
same key within 24 hours returns the FIRST result (the same charge id, or the
same decline) and is never charged twice. A request without a key is processed
every time it arrives. (A request answered 400 is not remembered.)

Keys must be unique per logical charge:

- every attempt of ONE charge carries the SAME key;
- two different charges never share a key — even when the customer, the
  amount and the memo are identical (a customer may really buy the same thing
  twice);
- reusing a key with a different body answers 409 and charges nothing.

If your program can stop part way and be run again (a batch job), keep the
key with your own record — for example, make it from the invoice number — so
that the run that resumes sends the same key for the same charge.

## Lost responses and retries

The network can lose the REQUEST (the server never saw it) or the RESPONSE
(the server made the charge, but the client never heard back). The client
cannot tell which one happened. So:

- on a timeout (no response) or a 503, send the same request again, with the
  same `Idempotency-Key`;
- make at most 3 attempts in total for one charge; when the third attempt
  fails too, give up and raise that last error;
- never retry any other answer (400, 402, 409): it will not change, and
  retrying a declined card can get the card blocked.

There is no need to wait between attempts.
