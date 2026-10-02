; Harness mode: PeTTa (Omega's MeTTa engine) + the Deal Desk plugin, WITHOUT the agent loop.
; No LLM, no API key. Used for development and the parity test before the full agent runs.
; Started by omega\start-omega.bat harness.
!(import! &self /PeTTa/dealdesk/plugin/dealdesk.py)
!(py-call (dealdesk.serve_forever))
