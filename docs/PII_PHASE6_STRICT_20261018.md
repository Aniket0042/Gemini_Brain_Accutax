# PII phase 6: strict sentinel, 18 Oct 2026

`PII_SENTINEL=strict` (with `PII_MODE=on`) makes the sentinel the last line of defence before any model call. It scans the payload as it is about to leave, and hides any name or identifier it still finds behind the request's placeholders. Then the call goes on. The user still sees the real value in the answer, and the masking learns the value, so later calls and the privacy snapshot hide it too. Nothing is blocked: if the check itself fails, the call goes as it was and the failure is logged. Each catch is logged as `strict_hid` in the sentinel log and as `pii_sentinel_caught` in the audit log, and counted in the request's `privacy.sentinel_caught`.

A unit test now fails the build if any module calls a model without going through `reasoning/bedrock_client.py`, so it cannot skip the sentinel. That covers a Bedrock runtime client, a raw `converse`/`invoke_model` call, or another provider's SDK. A second test checks that all five call paths in `bedrock_client.py` run the sentinel.

## VM check (production code plus the change, `/tmp/pii_strict`)

| Run | Model calls | Calls where strict hid something | Result |
| --- | ---: | ---: | --- |
| PII red-team, 43 cases | 86 | 0 | 0 of 43 leaked; 41 of 43 answer checks ok ($0.89) |
| 89 real questions | 166 | 0 | 89 of 89 answered, 81 grounded (81 before), 0 placeholders left ($2.40) |

Strict mode found nothing to hide in 252 calls, because the masking already catches everything the sentinel can recognise. So the payloads were exactly what report mode sends, and the answers cannot have changed because of it.

The figure-screen differences against the earlier replay (r045, r047 and three others) and the two red-team answer checks are run-to-run model variance:
- **pr34:** the model refused the "print the real names" injection and asked which "top 5" was meant, instead of listing.
- **pr35:** the user typed a placeholder themselves.

## Switching it on

Set `PII_SENTINEL=strict` in the VM `.env` and restart `accutax-ai-api`. To roll back, set it to `report`.
