# Reference - Partner API rate limits

Owner: API team, Larkspur Pay. Last reviewed 2026-10-02.

Each partner API key is limited to 240 requests per minute, with a burst allowance of 60 requests. Requests over the limit get HTTP 429 with a Retry-After header.

Raising a partner's limit needs a ticket in the PARTNER queue and approval from the API team lead, Mei Okonjo.
