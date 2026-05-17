from pyngrok import ngrok

def on_starting(server):
    bind_addr = server.cfg.bind
    port = int(bind_addr.split(":")[-1])
    public_url = ngrok.connect(port, "http")
    print(f"\n👉  ngrok tunnel established: {public_url}\n")
