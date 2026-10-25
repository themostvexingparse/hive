---
title: Pairing other computers and placing agents on them
keywords: machine, computer, computers, pair, pairing, pairing code, invite, placement, place, placed, join key, join keys, hk_, revoke, unpair, accept_placements, network
see: join, agents, up, server, security
---
# `hive machines`

A **machine** is any computer that runs agents. Pair a computer once, and from then on you
add agents to it from your own computer; it sets them up by itself. You never copy a token.

## Pairing a computer

On your computer:

```sh
hive machines invite
# √ Pairing code K7Q4-M2XD (works once, for 15 minutes)
#   hive join K7Q4-M2XD --server http://192.168.1.20:8765
```

On the other computer: clone the repository, install Hive and the agent CLIs, then inside
the clone run the printed line, and `hive up`:

```sh
hive join K7Q4-M2XD --server http://192.168.1.20:8765    # --as NAME to choose its name
hive up
```

It is named after its host name unless you pass `--as`, and remembers the clone you ran it
in. If your server only listens on your own computer, `invite` says so: other computers need
`hive server --host 0.0.0.0` (and the port open in the firewall), or a server online.

## Placing agents on it

```sh
hive agents add claude programmer --on desk
hive agents add 2 codex programmers --on desk
hive agents edit claude-3 --on laptop      # move it; --on none: off every paired computer
hive machines                              # paired computers: state, CLIs, agents
```

The other computer's daemon (`hive up`) asks the server every 30 seconds which agents are
placed on it. It **claims** each new one (the server gives it a fresh token that only that
computer ever sees), sets up its slot next to its clone, and takes off agents removed or
placed elsewhere, without a restart. Log each agent's account in there once:
`hive agents login NAME` (`hive up` offers it).

## Unpairing, and refusing placements

```sh
hive machines rm desk        # its token stops working, and so do its agents' tokens
```

The agents stay on the server, unplaced: place them elsewhere and they get new tokens there.
To make a computer refuse placements, set `accept_placements = "never"` in its
`~/.hive/machine.toml`.

## Many computers, from a script: join keys

```sh
hive machines invite --reusable --days 7 --label ci   # prints hk_…, shown once
hive machines keys                                    # keys that still work, and their use
hive machines revoke 3f9a1c2b07                       # stop it pairing more computers
hive join hk_… --server https://hive.example.com --as build-07   # on each computer
```

A join key pairs any number of computers until it expires or is revoked: keep it like a
password. A paired computer's own token (`hm_…`) can only ask which agents are placed on it
and claim them; it can't act as an agent or as you.
