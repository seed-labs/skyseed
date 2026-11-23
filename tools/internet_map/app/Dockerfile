FROM node:22-slim AS builder
RUN npm install -g pnpm@9.15.9
WORKDIR /build/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ .
RUN pnpm run build-test

FROM node:22-slim
WORKDIR /usr/src/app/backend
COPY backend/package*.json ./
RUN npm ci
COPY backend/ .
RUN ./node_modules/.bin/tsc
COPY --from=builder /build/frontend/dist ../frontend/dist
COPY LICENSE.txt /usr/src/app/LICENSE.txt
CMD ["node", "./bin/main.js"]
