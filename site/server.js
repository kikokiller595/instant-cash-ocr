const express = require("express");
const http = require("http");
const { Server } = require("socket.io");
const path = require("path");

const app = express();
const server = http.createServer(app);
const io = new Server(server);
const SITE = __dirname;
const PORT = Number(process.env.PORT || 3000);

app.use((req, _res, next) => {
  console.log("REQ", req.method, req.url);
  next();
});

app.use(express.static(SITE, { extensions: ["html"] }));

app.get("/", (_req, res) => res.sendFile(path.join(SITE, "index.html")));
app.get("/admin", (_req, res) => res.sendFile(path.join(SITE, "admin.html")));
app.get("/remote", (_req, res) => res.sendFile(path.join(SITE, "remote_states.htm")));
app.get("/obs", (_req, res) => res.sendFile(path.join(SITE, "index.html")));

io.on("connection", () => {});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`Listening on http://0.0.0.0:${PORT}`);
});
