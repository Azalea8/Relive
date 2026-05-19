// 虎牙弹幕采集 Worker — WebSocket Tars 协议
// 用法: node huya_worker.js <room_id>
// 输出: JSON 行格式到 stdout (与 douyu_worker.js 接口一致)
// 依赖: npm install ws

const WebSocket = require('ws');
const https = require('https');

const room_id = process.argv[2];
if (!room_id) {
    process.stderr.write('Usage: node huya_worker.js <room_id>\n');
    process.exit(1);
}

const WSS_URL = 'wss://cdnws.api.huya.com/';
const HEARTBEAT_INTERVAL = 60000;

// Pre-built heartbeat (Tars-encoded UserHeartBeatReq, identical to Python ref)
const HEARTBEAT = Buffer.from(
    '00031d0000690000006910032c3c4c56086f6e6c696e6575' +
    '69660f4f6e557365724865617274426561747d00003c0800' +
    '010604745265711d00002f0a0a0c1600260036076164725f' +
    '77617046000b1203aef00f2203aef00f3c426d5202605c60' +
    '017c82000bb01f9cac0b8c980ca80c', 'hex');

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

// ====================== Tars / JCE binary helpers ======================
// Type tags used by the Huya protocol
const T_INT32 = 0, T_INT64 = 1, T_STRING = 2, T_BOOL = 3, T_BYTES = 4;

class TarsOutputStream {
    constructor() { this._buf = Buffer.alloc(0); }
    getBuffer() { return this._buf; }
    _append(b) { this._buf = Buffer.concat([this._buf, b]); }
    _writeHead(tag, type) { this._append(Buffer.from([tag << 4 | (type & 0x0f)])); }
    _writeInt32(v) {
        v |= 0;
        const b = Buffer.alloc(4);
        b.writeInt32BE(v, 0);
        this._append(b);
    }
    _writeInt64(v) {
        const b = Buffer.alloc(8);
        b.writeBigInt64BE(BigInt(v), 0);
        this._append(b);
    }
    _writeString(s) {
        const encoded = Buffer.from(s, 'utf8');
        const len = Buffer.alloc(4);
        len.writeUInt32BE(encoded.length, 0);
        this._append(len);
        this._append(encoded);
    }
    writeInt32(tag, v)  { this._writeHead(tag, T_INT32); this._writeInt32(v); }
    writeInt64(tag, v)  { this._writeHead(tag, T_INT64); this._writeInt64(v); }
    writeString(tag, s) { this._writeHead(tag, T_STRING); this._writeString(s); }
    writeBool(tag, v)   { this._writeHead(tag, T_BOOL); this._append(Buffer.from([v ? 1 : 0])); }
    writeBytes(tag, b)  {
        this._writeHead(tag, T_BYTES);
        const len = Buffer.alloc(4);
        len.writeUInt32BE(b.length, 0);
        this._append(len);
        this._append(b);
    }
}

class TarsInputStream {
    constructor(buf) { this._buf = buf; this._pos = 0; }
    _readByte() { return this._buf[this._pos++]; }
    _readInt32() {
        const v = this._buf.readInt32BE(this._pos);
        this._pos += 4;
        return v;
    }
    _readInt64() {
        const v = this._buf.readBigInt64BE(this._pos);
        this._pos += 8;
        return Number(v);
    }
    _readStringLen(len) {
        const s = this._buf.slice(this._pos, this._pos + len).toString('utf8');
        this._pos += len;
        return s;
    }
    _readBytes() {
        const len = this._buf.readUInt32BE(this._pos);
        this._pos += 4;
        const b = this._buf.slice(this._pos, this._pos + len);
        this._pos += len;
        return b;
    }
    // Read a tagged field, return { tag, type, data }
    readField() {
        if (this._pos >= this._buf.length) return null;
        const head = this._readByte();
        const tag = head >> 4;
        const type = head & 0x0f;
        switch (type) {
            case T_INT32:  return { tag, type, data: this._readInt32() };
            case T_INT64:  return { tag, type, data: this._readInt64() };
            case T_STRING: return { tag, type, data: this._readStringLen(this._buf.readUInt32BE(this._pos - 4)) };
            case T_BOOL:   return { tag, type, data: this._readByte() !== 0 };
            case T_BYTES:  return { tag, type, data: this._readBytes() };
            default: return null;
        }
    }
    readInt32(tag)    { const f = this.readField(); return f && f.tag === tag ? f.data : 0; }
    readBytes(tag)     { const f = this.readField(); return f && f.tag === tag ? f.data : Buffer.alloc(0); }
    readInt64(tag)     { const f = this.readField(); return f && f.tag === tag ? f.data : 0; }
    readString(tag)    { const f = this.readField(); return f && f.tag === tag ? f.data : ''; }
}

// ====================== Huya protocol helpers ======================
function buildUserInfo(uid) {
    const oos = new TarsOutputStream();
    oos.writeInt64(0, uid);   // iUid
    oos.writeBool(1, false);   // bAnonymous
    oos.writeString(2, '');    // sGuid
    oos.writeString(3, '');    // sToken
    oos.writeInt64(4, 0);      // lTid
    oos.writeInt64(5, 0);      // lSid
    oos.writeInt64(6, uid);    // lGroupId
    oos.writeInt64(7, 3);      // lGroupType
    return oos;
}

function buildRegisterCmd(userInfoBuf) {
    const oos = new TarsOutputStream();
    oos.writeInt32(0, 1);     // iCmdType = EWSCmd_RegisterReq
    oos.writeBytes(1, userInfoBuf);
    return oos.getBuffer();
}

function decodeMessage(data) {
    const msgs = [];
    try {
        const ios = new TarsInputStream(data);
        const cmdType = ios.readInt32(0);
        if (cmdType === 7) {  // Push message
            const innerData = ios.readBytes(1);
            const iios = new TarsInputStream(innerData);
            const uri = iios.readInt64(1);
            if (uri === 1400) {  // Chat message
                const chatData = iios.readBytes(2);
                const cios = new TarsInputStream(chatData);
                // Read User struct (tag 0): string name, ignore int32 color
                const userData = cios.readBytes(0);
                if (userData.length > 0) {
                    const uios = new TarsInputStream(userData);
                    const name = uios.readString(0);
                    const content = cios.readString(3);
                    if (name && content) {
                        let color = 'ffffff';
                        try { color = (16777215 & uios.readInt32(1)).toString(16).padStart(6, '0'); } catch(e) {}
                        msgs.push({ msg_type: 'chat', content, username: name, color });
                    }
                }
            }
        }
    } catch (e) {
        process.stderr.write(`[huya] decode: ${e.message}\n`);
    }
    return msgs;
}

// ====================== Main ======================
function get_uid() {
    return new Promise((resolve) => {
        https.get(`https://www.huya.com/${room_id}`, {
            timeout: 15000,
            headers: { 'User-Agent': UA, 'Referer': 'https://www.huya.com/' }
        }, (res) => {
            let body = '';
            res.on('data', d => body += d);
            res.on('end', () => {
                let m = body.match(/"uid":\s*"?(\d+)"?/);
                if (!m) m = body.match(/ayyuid:\s*'(\d+)'/);
                if (!m) m = body.match(/profileRoom.*?(\d{5,})/);
                if (!m) {
                    process.stderr.write(`[huya] uid not found, trying anonymous\n`);
                    return resolve(0);
                }
                resolve(parseInt(m[1]));
            });
        }).on('error', () => resolve(0));
    });
}

async function start() {
    const uid = await get_uid();
    if (!uid) {
        process.stderr.write('[huya] failed to extract uid from page\n');
        process.exit(1);
    }
    process.stderr.write(`[huya] uid=${uid}, connecting\n`);

    const uinfo = buildUserInfo(uid);
    const regCmd = buildRegisterCmd(uinfo.getBuffer());

    const client = new WebSocket(WSS_URL);
    let hb_timer;

    client.on('open', () => {
        client.send(regCmd);
        hb_timer = setInterval(() => client.send(HEARTBEAT), HEARTBEAT_INTERVAL);
    });

    client.on('message', (data) => {
        const msgs = decodeMessage(data);
        msgs.forEach(m => {
            m.timestamp_ms = Date.now();
            process.stdout.write(JSON.stringify(m) + '\n');
        });
    });

    client.on('error', (e) => {
        process.stderr.write(`[huya] ws error: ${e.message}\n`);
    });

    client.on('close', () => {
        clearInterval(hb_timer);
        process.stderr.write('[huya] ws closed, exiting\n');
        process.exit(0);
    });
}

process.stderr.write(`[huya] starting for room=${room_id}\n`);
start().catch(e => {
    process.stderr.write(`[huya] fatal: ${e.message}\n`);
    process.exit(1);
});
