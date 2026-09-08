FROM node:22-alpine

WORKDIR /app

# Deps de website (por separado)
COPY website/my-app/package.json website/my-app/package-lock.json* ./uis/website/
RUN npm install --prefix ./uis/website

# Deps de backoffice (por separado)
COPY backoffice/package.json backoffice/package-lock.json* ./uis/backoffice/
RUN npm install --prefix ./uis/backoffice

COPY start.sh ./start.sh
RUN chmod +x ./start.sh

EXPOSE 3000 3001

CMD ["./start.sh"]