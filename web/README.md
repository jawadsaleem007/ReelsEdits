# web

Next.js 15 app. Upload → style card → coverage → preview → export.

```bash
npm install && npm run dev     # http://localhost:3000
```

## The two screens that matter

**Style card** (`app/style-card/`) — where the user decides whether to trust the
system. It shows a *readable description* of the extracted style, not a progress
bar, and it shows no frames from the reference. It reframes the interaction from
"black box magic" to "here is a specification you can inspect", which is what
makes the later editing affordances feel natural rather than like error
correction. See [docs/01 §3.1](../docs/01-product-vision.md).

**Swap UI** (`app/editor/`) — where the matcher's training data comes from.
Every swap is a preference pair labelled by a domain expert at the moment of
peak engagement. This screen must be *pleasant*, not merely present, and every
alternative must carry its `reason` and score `breakdown` — an explained choice
produces an informative correction, an unexplained one produces a random click.
See [docs/09 §6](../docs/09-clip-matching.md).

## Upload

Direct-to-S3 via presigned multipart (`lib/upload.ts`). Bytes never touch the
API. Pre-flight checks read duration, resolution and codec from the file header
before a single byte is sent — rejecting a 4GB file after upload is a terrible
experience.
