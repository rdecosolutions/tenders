# Putting this on a server

Right now everything dies when your Mac sleeps. This moves the engine and
the app onto a small always-on machine so tenders keep arriving and, later,
notifications can actually fire.

**Cost:** about ₹550–750/month total — roughly €4/mo for the server and
~₹1,000/year for a domain.

**Time:** about 30 minutes of your attention, then 2–3 hours of the first
backfill running by itself.

---

## What you need to do (I can't — these need your card)

### 1. Create the server

**Hetzner Cloud** is the best value: sign up at console.hetzner.cloud, then
Create Server →

- Location: **Nuremberg** or **Helsinki** (or Singapore if you prefer closer)
- Image: **Ubuntu 24.04**
- Type: **CX22** (2 vCPU, 4 GB RAM) — €3.79/mo

> Don't go below 2 GB of RAM. The scraper runs a real Chromium browser and
> a 1 GB box will run out of memory partway through a sweep.

Add your SSH key if you have one; otherwise Hetzner emails you a root
password. Note the server's **IP address**.

DigitalOcean works equally well — pick a $12/mo 2 GB droplet, not the $6
1 GB one, for the same reason.

### 2. Get a domain

Any registrar (Namecheap, Cloudflare, GoDaddy). `tenders.yourdomain.com` is
fine — a subdomain of a domain you already own costs nothing extra.

In the registrar's DNS settings add one record:

| Type | Name      | Value              |
|------|-----------|--------------------|
| A    | `tenders` | your server's IP   |

Wait a few minutes for it to take effect.

> The domain is genuinely required, not decoration: HTTPS certificates are
> issued to names, not IP addresses, and Web Push notifications refuse to
> work without HTTPS.

---

## Then run these (copy-paste)

From your Mac, copy the project up and log in:

```bash
rsync -av --exclude data --exclude '*.orig' --exclude logs \
  ~/Desktop/tn-tender-engine/ root@YOUR_SERVER_IP:/opt/tn-tender/
```

```bash
ssh root@YOUR_SERVER_IP
```

On the server, run the setup script with your domain and email:

```bash
cd /opt/tn-tender/deploy && bash setup.sh tenders.yourdomain.com you@email.com
```

It asks you to choose a password — that's what you'll type when you open the
site. Then start the first backfill:

```bash
systemctl start tn-tender-engine.service
```

That's it. Open `https://tenders.yourdomain.com`, log in as `kim`, and
tenders appear as the backfill fills them in.

---

## What the setup script sets up

- **The app** runs constantly and restarts itself if it ever crashes.
- **The engine** runs at **02:30 every night**, with a random delay of up to
  20 minutes so we're not hitting the portal at the same instant daily. If
  the server was off at 02:30 it runs when it comes back, rather than
  skipping the day.
- **Caddy** sits in front, gets a real HTTPS certificate automatically and
  renews it forever, and asks for the password.
- The app listens only on localhost, so the only thing reachable from the
  internet is Caddy.

## Useful commands

```bash
systemctl status tn-tender-app
```

```bash
journalctl -u tn-tender-engine -n 50
```

```bash
systemctl list-timers tn-tender*
```

```bash
systemctl start tn-tender-engine
```

## Updating it later

```bash
rsync -av --exclude data ~/Desktop/tn-tender-engine/ root@YOUR_SERVER_IP:/opt/tn-tender/ && ssh root@YOUR_SERVER_IP 'systemctl restart tn-tender-app'
```

The `--exclude data` matters: it stops your Mac's copy from overwriting the
server's database, which is the accumulated history.

## A note on the database

SQLite is right for this. One process writes (the nightly engine), a few
people read (the app), and WAL mode already lets those happen at once.
Postgres only starts earning its extra complexity when several people write
at the same time, which isn't this.
