FROM node:22-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
COPY middleware.ts ./
COPY app ./app
COPY components ./components
COPY public ./public
COPY next.config.ts ./
COPY tsconfig.json ./
COPY next-env.d.ts ./
COPY .npmrc ./
COPY pnpm-workspace.yaml ./
COPY pnpm-lock.yaml ./
RUN npm install
EXPOSE 3000
CMD ["npm", "run", "dev"]

