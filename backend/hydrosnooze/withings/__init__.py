"""The Withings Sleep Analyzer: the first measurement of the sleeper rather than
the machine.

Four parts, kept apart on purpose:

    client.py   talking to Withings, and nothing else
    parse.py    turning what it sends into nights, stages and minutes
    sync.py     the loop that fetches them, beside the bed and never in its way
    health.py   the Health Report, built from what is stored

**The bed never depends on any of this.** It runs in its own loop, never takes
the command lock, and every failure in it ends as a missing chart rather than a
cold bed. docs/withings.md is what the API actually does, established against
the live API and seven real nights, and the traps the parser steps round.
"""
