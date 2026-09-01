# The torve operator surface

One dependency-free HTML file (`index.html`) — vanilla JS over the two
`torve serve` endpoints, no framework, no build toolchain (RFC 0032,
amended by A-76). `scripts/build.sh` copies it into `src/torve/_web/`,
which ships as wheel package data. Edit `index.html`, run the script,
reload the page.
