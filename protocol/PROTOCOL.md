# CloudLink protocol v2 (LAN only — no server, no internet)

Every device is both a **server** (TCP listener, default port 47616) and a **client**.
Android implements the same thing in Kotlin (`ServerSocket` + foreground service).

## Discovery (UDP broadcast, port 47615)
Every 2 s: `{"app":"cloudlink","v":2,"id":"<deviceId>","name":"<display name>","port":<tcp port>}`
sent to 255.255.255.255:47615 and received on the same port. Unauthenticated hint only (address + label);
entries expire after 8 s. Manual "connect by IP" must also be offered (some routers block broadcast / isolate clients).

## Frames (TCP)
`u32 BE length(type+payload) | u8 type | payload`   type 0 = JSON handshake (<=4 KiB), 1 = control blob (ASCII base64), 2 = file chunk frame. Max frame 1 MiB.

## Pairing (first time only)
Both screens show a 6-digit SAS; the users compare them.
1. I→R `pair_req {id,name,port,commit}` with `commit = sha256("cloudlink/pair/v2" ‖ pubI ‖ nonceI)` (hex).
2. R shows "X wants to pair"; on accept R→I `pair_resp {id,name,pub}` (X25519, base64).
3. I→R `pair_reveal {pub, nonce}`; R checks the commitment (else `pair_failed: commit_mismatch`).
4. Both: `shared = X25519`, `derive_session(shared)` → authKey, encKey, SAS (HKDF-SHA256, empty salt; infos `cloudlink/auth/v1`, `cloudlink/enc/v1`, `cloudlink/sas/v1`).
5. Both show SAS; user confirms → `pair_confirm {h = sha256hex(authKey)}`; each side verifies the other's `h`; then stores `{id → name, authKey, encKey, host, port}`.
Committing to the key before seeing the peer's key prevents a MITM from grinding keys until the 6 digits match.

## Session (every connection, mutual authentication)
```
C→S {"t":"hello","v":2,"id":idC,"nc":hex16}
S→C {"t":"hello_ok","ns":hex16,"mac":HMAC(auth,"srv"|idC|idS|nc|ns)}   // else {"t":"error","code":"not_paired"}
C→S {"t":"auth","mac":HMAC(auth,"cli"|idC|idS|nc|ns)}                 // else {"t":"error","code":"auth_failed"}
sessionKey = HKDF(encKey, info="cloudlink/sess/v2"|nc|ns)
```
`HMAC(key,label|parts…)` = HMAC-SHA256 over `u16BE len(label)|label` then each part as `u16BE len|bytes`. Test vectors: `test_vectors.json → v2`.
After the handshake, control messages (`seal_ctl`) and chunks (`xfer_key`) use `sessionKey` in place of encKey.

## Control messages (JSON inside AES-256-GCM, AAD `cloudlink/ctl/v1`)
`list{id,path,offset}` → `list_result{id,ok,entries[{n,d,s,m}],more}` · `get{id,path}` → `offer…`/`get_error` ·
`offer{tid,name,size,[dest],[reply_to]}` · `done{tid,chunks,size,sha256}` · `ack{tid,ok,[error]}` · `cancel{tid,[error]}`.

* `dest` = folder inside the receiver's shared storage to put the file in (upload). Without `dest` the file goes to the receiver's download folder.
* Receiver rules: paths resolved under the share root (no `..`, no symlink escape), names sanitised, dot-files hidden, uploads refused when read-only (`forbidden`), written as `.part` then renamed after size+sha256 match.
