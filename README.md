# Arduino-Project-Manager-GPT
A complete customized GPT that has access to your Arduino/ folder to compile, upload, create, debug, manage libraries and board cores, automate build processes and much more!

[![Watch the video](thumbnail.jpg)](https://www.youtube.com/watch?v=Hhlq1Eq2puk)

The launch the `server.py` use `uvicorn`:
```bash
Arduino-Project-Manager-GPT: $ uvicorn server:app --host 127.0.0.1 --port 8000 &
```
you will see the server start as a background task. Press return to get back to a command prompt:

```bash
Arduino-Project-Manager-GPT: $ uvicorn server:app --host 127.0.0.1 --port 8000 &
[1] 13977
Arduino-Project-Manager-GPT: $ 2025-02-24 22:39:02,714 - INFO - Arduino projects directory set to: /Users/username/Documents/Arduino
2025-02-24 22:39:02,715 - INFO - Building initial project cache...
2025-02-24 22:39:02,989 - INFO - Initial cache built with 192 projects.
INFO:     Started server process [13977]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)

Arduino-Project-Manager-GPT: $
```
then start `ngrok` to tunnel the intranet socket to an internet URL visible socket that openAI can see and attach to:

```bash
Arduino-Project-Manager-GPT: $ ngrok http 8000
```
and the ngrok screen will be displayed:

```bash
ngrok                                                                                                                                                (Ctrl+C to quit)

🐛 Found a bug? Let us know: https://github.com/ngrok/ngrok

Session Status                online
Account                       Ripred (Plan: Free)
Version                       3.20.0
Region                        United States (us)
Latency                       45ms
Web Interface                 http://127.0.0.1:4040
Forwarding                    https://46c2-70-119-126-223.ngrok-free.app -> http://localhost:8000

Connections                   ttl     opn     rt1     rt5     p50     p90
                              0       0       0.00    0.00    0.00    0.00
```

**Note that the forwarding url will change every time you start `ngrok` with a free subscription.**

Copy the forwarding address and paste it into the openAI Custom GPT yaml configuration where it asks for the url:

```bash
==> openai.yaml <==
openapi: 3.1.0
info:
  title: Arduino Project Manager API
  description: API for managing Arduino projects using arduino-cli, with cached file listing and just-in-time file reading.
  version: 2.0.0
servers:
  - url: [PLACE-YOUR-NGROK-URL-HERE]
    description: Ngrok tunnel URL for the FastAPI server
...
<snip>
```

so that it looks like this (using the example url above: `https://46c2-70-119-126-223.ngrok-free.app`)
```bash
==> openai.yaml <==
openapi: 3.1.0
info:
  title: Arduino Project Manager API
  description: API for managing Arduino projects using arduino-cli, with cached file listing and just-in-time file reading.
  version: 2.0.0
servers:
  - url: https://46c2-70-119-126-223.ngrok-free.app
    description: Ngrok tunnel URL for the FastAPI server
...
<snip>
```

At this point your Custom GPT will be ready to test, modify, and publish!

**To Exit `ngrok`**
Hit ctrl-c to stop and exit `ngrok`.

The make the `uvicorn` process the foreground, and hit ctrl-c again to exit `uvicorn`.

```bash
Arduino-Project-Manager-GPT: $ ngrok http 8000
Arduino-Project-Manager-GPT: $
Arduino-Project-Manager-GPT: $
Arduino-Project-Manager-GPT: $ fg
uvicorn server:app --host 127.0.0.1 --port 8000
^CINFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [13977]
Arduino-Project-Manager-GPT: $
```
