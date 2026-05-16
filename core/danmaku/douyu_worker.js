// 斗鱼弹幕采集 Worker — 基于 douyudm 协议实现
// 用法: node danmaku_worker.js <room_id>
// 输出: JSON 行格式到 stdout
// 断线自动重连，收到 SIGINT/SIGTERM 退出

const WebSocket = require('ws');

const room_id = process.argv[2];
if (!room_id) {
  process.stderr.write('Usage: node danmaku_worker.js <room_id>\n');
  process.exit(1);
}

const ports = [8501, 8502, 8503, 8504, 8505, 8506];
const MAX_RECONNECT = 20;
const RECONNECT_WAIT = 3000;

// STT 序列化
function sttEscape(v) {
  return String(v).replace(/@/g, '@A').replace(/\//g, '@S');
}

function sttSerialize(obj) {
  return Object.entries(obj)
    .map(([k, v]) => `${k}@=${sttEscape(v)}/`)
    .join('');
}

// 二进制包编码
function encode(msg) {
  const body = Buffer.from(msg, 'utf8');
  const nullTerm = Buffer.from([0x00]);
  const bodyWithNull = Buffer.concat([body, nullTerm]);
  const messageLength = bodyWithNull.length + 8;

  const header = Buffer.alloc(12);
  header.writeUInt32LE(messageLength, 0);
  header.writeUInt32LE(messageLength, 4);
  header.writeUInt16LE(689, 8);
  header.writeUInt16LE(0, 10);

  return Buffer.concat([header, bodyWithNull]);
}

// 二进制包解码
function decode(buf, callback) {
  let offset = 0;
  while (offset < buf.length) {
    if (offset + 4 > buf.length) break;
    const readLength = buf.readUInt32LE(offset);
    offset += 4;

    if (offset + readLength - 4 > buf.length) break;
    const message = buf.slice(offset + 8, offset + readLength - 1).toString('utf8');
    offset += readLength - 4;
    callback(message);
  }
}

// STT 反序列化
function sttDeserialize(raw) {
  if (raw.includes('//')) {
    return raw.split('//').filter(e => e !== '').map(item => sttDeserialize(item));
  }
  if (raw.includes('@=')) {
    const result = {};
    raw.split('/').filter(e => e !== '').forEach(s => {
      const idx = s.indexOf('@=');
      if (idx !== -1) {
        const k = s.slice(0, idx);
        const v = s.slice(idx + 2);
        result[k] = v ? sttDeserialize(v) : '';
      }
    });
    return result;
  }
  return raw.replace(/@S/g, '/').replace(/@A/g, '@');
}

const COLOR_TAB = {
  '2': '00ccff',
  '3': '66ff00',
  '4': 'ff6600',
  '6': 'f6447f',
  '5': 'cc00ff',
  '1': 'ff2e2e',
};

function mapMsgType(type) {
  const map = { dgb: 'gift', chatmsg: 'danmaku', uenter: 'enter' };
  return map[type] || 'other';
}

const HEARTBEAT = encode(sttSerialize({ type: 'mrkl' }));

// 连接状态
let ws = null;
let heartbeatInterval = null;
let reconnectCount = 0;
let closing = false;

function connect() {
  const port = ports[Math.floor(Math.random() * ports.length)];
  const wsUrl = `wss://danmuproxy.douyu.com:${port}/`;

  process.stderr.write(`Connecting to ${wsUrl} room=${room_id}\n`);
  ws = new WebSocket(wsUrl);

  ws.on('open', () => {
    process.stderr.write('Connected\n');
    reconnectCount = 0;

    ws.send(encode(sttSerialize({ type: 'loginreq', roomid: String(room_id) })));
    ws.send(encode(sttSerialize({ type: 'joingroup', rid: String(room_id), gid: '-9999' })));
    process.stderr.write('Login + Join sent\n');

    heartbeatInterval = setInterval(() => {
      try { ws.send(HEARTBEAT); } catch (e) {}
    }, 45000);
  });

  ws.on('message', (data) => {
    const buf = Buffer.isBuffer(data) ? data : Buffer.from(data);
    decode(buf, (raw) => {
      try {
        const msg = sttDeserialize(raw);
        if (!msg || !msg.type) return;

        const type = msg.type;

        if (type === 'chatmsg') {
          const out = {
            timestamp_ms: Date.now(),
            content: msg.txt || '',
            username: msg.nn || '',
            msg_type: 'chat',
            color: COLOR_TAB[msg.col] || 'ffffff',
            uid: msg.uid || '',
            level: msg.level || '',
          };
          process.stdout.write(JSON.stringify(out) + '\n');
        } else if (type === 'dgb') {
          const out = {
            timestamp_ms: Date.now(),
            content: `送出 ${msg.gfn || '礼物'}`,
            username: msg.nn || '',
            msg_type: 'gift',
            gift_id: msg.gfid || '',
            gift_name: msg.gfn || '',
            gift_count: msg.gc || '1',
          };
          process.stdout.write(JSON.stringify(out) + '\n');
        }
      } catch (e) {}
    });
  });

  ws.on('close', () => {
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    if (closing) {
      process.stderr.write('Connection closed (graceful)\n');
      process.exit(0);
    }

    reconnectCount++;
    if (reconnectCount > MAX_RECONNECT) {
      process.stderr.write(`Reconnect failed after ${MAX_RECONNECT} attempts\n`);
      process.exit(1);
    }

    process.stderr.write(`Connection closed, reconnecting (${reconnectCount}/${MAX_RECONNECT}) in ${RECONNECT_WAIT/1000}s...\n`);
    setTimeout(connect, RECONNECT_WAIT);
  });

  ws.on('error', (err) => {
    process.stderr.write(`Error: ${err.message}\n`);
  });
}

// 启动连接
connect();

// 优雅退出
function gracefulExit() {
  closing = true;
  if (heartbeatInterval) clearInterval(heartbeatInterval);
  try { ws.send(encode(sttSerialize({ type: 'logout' }))); } catch (e) {}
  ws.close();
}
process.on('SIGINT', gracefulExit);
process.on('SIGTERM', gracefulExit);
