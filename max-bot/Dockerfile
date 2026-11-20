FROM node:22-bookworm-slim AS build

WORKDIR /app

COPY max-bot/package.json max-bot/package-lock.json ./
RUN npm ci

COPY max-bot/tsconfig.json max-bot/tsconfig.build.json ./
COPY max-bot/src ./src
RUN npm run build && npm prune --omit=dev

FROM node:22-bookworm-slim AS runtime

ENV NODE_ENV=production \
    NODE_EXTRA_CA_CERTS=/app/certs/russian_trusted_root_ca.pem

WORKDIR /app

COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/dist ./dist
COPY max-bot/certs ./certs

RUN useradd --system --create-home --uid 10001 app \
    && chown -R app:app /app

USER app

EXPOSE 3000

CMD ["node", "dist/src/index.js"]
