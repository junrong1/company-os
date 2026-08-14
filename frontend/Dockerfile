# syntax=docker/dockerfile:1
#
# The `web` service of the demo profile: build the client, then serve the built
# output. Node is a build-time dependency only — the running container is nginx
# with static files, which is why the demo profile needs no host toolchain.

FROM node:26-bookworm-slim AS build
WORKDIR /app

# Dependencies first so editing source does not reinstall them.
COPY package.json package-lock.json* ./
# `npm ci` when a lockfile is present, `npm install` on first build before one
# has been committed. Both are reproducible against the pins in package.json.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY tsconfig*.json vite.config.ts index.html ./
COPY public/ public/
COPY src/ src/
COPY tests/ tests/

# `tsc -b && vite build`: the type check is part of the build on purpose, so a
# type error fails the image rather than shipping.
RUN npm run build


FROM nginx:1.29-alpine AS web
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8790
