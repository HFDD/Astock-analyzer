const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const url = require('url');

const PORT = 3927;

// ===== 数据获取 =====

function fetchUrl(targetUrl) {
  return new Promise((resolve, reject) => {
    const mod = targetUrl.startsWith('https') ? https : http;
    const req = mod.get(targetUrl, { 
      headers: { 
        'Referer': 'https://finance.sina.com.cn',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
      },
      timeout: 10000 
    }, (res) => {
      let data = '';
      res.setEncoding('utf-8');
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve(data));
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

// 获取实时行情（腾讯API）
async function getRealtimeQuote(code) {
  const prefix = code.startsWith('6') || code.startsWith('9') ? 'sh' : 'sz';
  const symbol = prefix + code;
  
  // 优先用腾讯API
  try {
    const url = `http://qt.gtimg.cn/q=${symbol}`;
    const data = await fetchUrl(url);
    const match = data.match(/"([^"]+)"/);
    if (match && match[1]) {
      const parts = match[1].split('~');
      if (parts.length > 40) {
        const price = parseFloat(parts[3]);
        const prevClose = parseFloat(parts[4]);
        return {
          code: code,
          name: parts[1],
          open: parseFloat(parts[5]),
          prevClose: prevClose,
          price: price,
          high: parseFloat(parts[33]),
          low: parseFloat(parts[34]),
          volume: parseInt(parts[6]),
          amount: parseFloat(parts[37]) * 10000,
          date: parts[30]?.substring(0, 8) || '',
          time: parts[30]?.substring(8) || '',
          change: price - prevClose,
          changePercent: ((price - prevClose) / prevClose * 100).toFixed(2),
          turnover: parseFloat(parts[38]) || 0
        };
      }
    }
  } catch (e) {
    console.log('腾讯API失败，尝试新浪...', e.message);
  }
  
  // 备用：新浪API
  try {
    const url = `http://hq.sinajs.cn/list=${symbol}`;
    const data = await fetchUrl(url);
    const match = data.match(/"([^"]+)"/);
    if (!match || !match[1]) return null;
    const parts = match[1].split(',');
    if (parts.length < 32) return null;
    return {
      code: code,
      name: parts[0],
      open: parseFloat(parts[1]),
      prevClose: parseFloat(parts[2]),
      price: parseFloat(parts[3]),
      high: parseFloat(parts[4]),
      low: parseFloat(parts[5]),
      volume: parseInt(parts[8]),
      amount: parseFloat(parts[9]),
      date: parts[30],
      time: parts[31],
      change: parseFloat(parts[3]) - parseFloat(parts[2]),
      changePercent: ((parseFloat(parts[3]) - parseFloat(parts[2])) / parseFloat(parts[2]) * 100).toFixed(2),
      turnover: 0
    };
  } catch (e) {
    console.error('所有API都失败:', e.message);
    return null;
  }
}

// 获取历史K线数据
async function getHistoryKline(code, days = 120) {
  const prefix = code.startsWith('6') || code.startsWith('9') ? 'sh' : 'sz';
  const symbol = prefix + code;
  
  // 尝试腾讯财经API获取历史数据
  try {
    const url = `http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=${symbol},day,,,${days},qfq`;
    const data = await fetchUrl(url);
    const json = JSON.parse(data);
    
    if (json.code === 0 && json.data && json.data[symbol]) {
      const klines = json.data[symbol].day || json.data[symbol].qfqday;
      if (klines && klines.length > 0) {
        return klines.map(k => ({
          date: k[0],
          open: parseFloat(k[1]),
          close: parseFloat(k[2]),
          high: parseFloat(k[3]),
          low: parseFloat(k[4]),
          volume: parseFloat(k[5])
        }));
      }
    }
  } catch (e) {
    console.log('腾讯API失败，尝试新浪...');
  }
  
  // 备用：新浪K线API
  try {
    const endDate = new Date().toISOString().split('T')[0];
    const url = `https://quotes.sina.cn/cn/api/jsonp.php/var/KC_MarketDataService.getKLineData?symbol=${symbol}&scale=240&ma=no&datalen=${days}`;
    const data = await fetchUrl(url);
    const match = data.match(/\((\[[\s\S]*\])\)/);
    if (match) {
      const klines = JSON.parse(match[1]);
      return klines.map(k => ({
        date: k.day || k.date,
        open: parseFloat(k.open),
        close: parseFloat(k.close || k.ma_price3),
        high: parseFloat(k.high),
        low: parseFloat(k.low),
        volume: parseFloat(k.volume)
      }));
    }
  } catch (e) {
    console.log('新浪K线API也失败');
  }
  
  return [];
}

// ===== 技术指标计算 =====

// 移动平均线
function calcMA(prices, period) {
  const result = [];
  for (let i = 0; i < prices.length; i++) {
    if (i < period - 1) {
      result.push(null);
    } else {
      let sum = 0;
      for (let j = 0; j < period; j++) {
        sum += prices[i - j];
      }
      result.push(+(sum / period).toFixed(2));
    }
  }
  return result;
}

// MACD
function calcMACD(closes, fast = 12, slow = 26, signal = 9) {
  const emaFast = calcEMA(closes, fast);
  const emaSlow = calcEMA(closes, slow);
  
  const dif = [];
  for (let i = 0; i < closes.length; i++) {
    if (emaFast[i] !== null && emaSlow[i] !== null) {
      dif.push(+(emaFast[i] - emaSlow[i]).toFixed(4));
    } else {
      dif.push(null);
    }
  }
  
  const validDif = dif.filter(d => d !== null);
  const deaRaw = calcEMA(validDif, signal);
  const dea = [];
  let j = 0;
  for (let i = 0; i < dif.length; i++) {
    if (dif[i] === null) {
      dea.push(null);
    } else {
      dea.push(deaRaw[j] !== undefined ? +deaRaw[j].toFixed(4) : null);
      j++;
    }
  }
  
  const macd = [];
  for (let i = 0; i < closes.length; i++) {
    if (dif[i] !== null && dea[i] !== null) {
      macd.push(+((dif[i] - dea[i]) * 2).toFixed(4));
    } else {
      macd.push(null);
    }
  }
  
  return { dif, dea, macd };
}

function calcEMA(data, period) {
  const result = [];
  const multiplier = 2 / (period + 1);
  
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null);
    } else if (i === period - 1) {
      let sum = 0;
      for (let j = 0; j < period; j++) sum += data[j];
      result.push(sum / period);
    } else {
      result.push((data[i] - result[i - 1]) * multiplier + result[i - 1]);
    }
  }
  return result;
}

// RSI
function calcRSI(closes, period = 14) {
  const result = [null];
  let avgGain = 0, avgLoss = 0;
  
  for (let i = 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    
    if (i < period) {
      avgGain += gain;
      avgLoss += loss;
      result.push(null);
    } else if (i === period) {
      avgGain = (avgGain + gain) / period;
      avgLoss = (avgLoss + loss) / period;
      const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
      result.push(+(100 - 100 / (1 + rs)).toFixed(2));
    } else {
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
      const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
      result.push(+(100 - 100 / (1 + rs)).toFixed(2));
    }
  }
  return result;
}

// KDJ
function calcKDJ(highs, lows, closes, period = 9) {
  const k = [], d = [], j = [];
  
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) {
      k.push(50); d.push(50); j.push(50);
      continue;
    }
    
    let highMax = -Infinity, lowMin = Infinity;
    for (let p = i - period + 1; p <= i; p++) {
      highMax = Math.max(highMax, highs[p]);
      lowMin = Math.min(lowMin, lows[p]);
    }
    
    const rsv = highMax === lowMin ? 50 : ((closes[i] - lowMin) / (highMax - lowMin)) * 100;
    
    const prevK = k.length > 0 ? k[k.length - 1] : 50;
    const prevD = d.length > 0 ? d[d.length - 1] : 50;
    
    const curK = +(2 / 3 * prevK + 1 / 3 * rsv).toFixed(2);
    const curD = +(2 / 3 * prevD + 1 / 3 * curK).toFixed(2);
    const curJ = +(3 * curK - 2 * curD).toFixed(2);
    
    k.push(curK);
    d.push(curD);
    j.push(curJ);
  }
  
  return { k, d, j };
}

// 布林带
function calcBOLL(closes, period = 20) {
  const ma = calcMA(closes, period);
  const upper = [], lower = [];
  
  for (let i = 0; i < closes.length; i++) {
    if (ma[i] === null) {
      upper.push(null);
      lower.push(null);
    } else {
      let sumSq = 0;
      for (let j = 0; j < period; j++) {
        sumSq += Math.pow(closes[i - j] - ma[i], 2);
      }
      const std = Math.sqrt(sumSq / period);
      upper.push(+(ma[i] + 2 * std).toFixed(2));
      lower.push(+(ma[i] - 2 * std).toFixed(2));
    }
  }
  
  return { ma, upper, lower };
}

// ===== 综合评分与建议 =====

function analyzeStock(quote, klines) {
  if (!klines || klines.length < 30) {
    return { score: 50, suggestion: '数据不足', signals: [] };
  }
  
  const closes = klines.map(k => k.close);
  const highs = klines.map(k => k.high);
  const lows = klines.map(k => k.low);
  const volumes = klines.map(k => k.volume);
  
  const ma5 = calcMA(closes, 5);
  const ma10 = calcMA(closes, 10);
  const ma20 = calcMA(closes, 20);
  const ma60 = calcMA(closes, 60);
  const macd = calcMACD(closes);
  const rsi = calcRSI(closes, 14);
  const kdj = calcKDJ(highs, lows, closes);
  const boll = calcBOLL(closes);
  
  const last = closes.length - 1;
  const price = closes[last];
  let score = 50;
  const signals = [];
  
  // === MA信号 ===
  if (ma5[last] && ma10[last]) {
    if (ma5[last] > ma10[last] && ma5[last - 1] <= ma10[last - 1]) {
      score += 15;
      signals.push({ type: 'buy', indicator: 'MA', text: 'MA5上穿MA10（金叉）', strength: 'strong' });
    } else if (ma5[last] < ma10[last] && ma5[last - 1] >= ma10[last - 1]) {
      score -= 15;
      signals.push({ type: 'sell', indicator: 'MA', text: 'MA5下穿MA10（死叉）', strength: 'strong' });
    }
    
    if (price > ma5[last] && price > ma10[last] && price > ma20[last]) {
      score += 10;
      signals.push({ type: 'buy', indicator: 'MA', text: '股价在MA5/10/20之上，多头排列', strength: 'medium' });
    } else if (price < ma5[last] && price < ma10[last] && price < ma20[last]) {
      score -= 10;
      signals.push({ type: 'sell', indicator: 'MA', text: '股价在MA5/10/20之下，空头排列', strength: 'medium' });
    }
  }
  
  if (ma60[last] && price > ma60[last]) {
    score += 5;
    signals.push({ type: 'buy', indicator: 'MA', text: '股价在60日均线之上', strength: 'weak' });
  } else if (ma60[last] && price < ma60[last]) {
    score -= 5;
    signals.push({ type: 'sell', indicator: 'MA', text: '股价在60日均线之下', strength: 'weak' });
  }
  
  // === MACD信号 ===
  if (macd.dif[last] !== null && macd.dea[last] !== null) {
    if (macd.dif[last] > macd.dea[last] && macd.dif[last - 1] <= macd.dea[last - 1]) {
      score += 15;
      signals.push({ type: 'buy', indicator: 'MACD', text: 'MACD金叉', strength: 'strong' });
    } else if (macd.dif[last] < macd.dea[last] && macd.dif[last - 1] >= macd.dea[last - 1]) {
      score -= 15;
      signals.push({ type: 'sell', indicator: 'MACD', text: 'MACD死叉', strength: 'strong' });
    }
    
    if (macd.macd[last] > 0 && macd.macd[last] > macd.macd[last - 1]) {
      score += 5;
      signals.push({ type: 'buy', indicator: 'MACD', text: 'MACD红柱放大', strength: 'weak' });
    } else if (macd.macd[last] < 0 && macd.macd[last] < macd.macd[last - 1]) {
      score -= 5;
      signals.push({ type: 'sell', indicator: 'MACD', text: 'MACD绿柱放大', strength: 'weak' });
    }
  }
  
  // === RSI信号 ===
  if (rsi[last] !== null) {
    if (rsi[last] < 30) {
      score += 10;
      signals.push({ type: 'buy', indicator: 'RSI', text: `RSI=${rsi[last]}，超卖区域`, strength: 'medium' });
    } else if (rsi[last] > 70) {
      score -= 10;
      signals.push({ type: 'sell', indicator: 'RSI', text: `RSI=${rsi[last]}，超买区域`, strength: 'medium' });
    } else if (rsi[last] > 50 && rsi[last] < 70) {
      score += 3;
      signals.push({ type: 'buy', indicator: 'RSI', text: `RSI=${rsi[last]}，偏强`, strength: 'weak' });
    } else if (rsi[last] < 50 && rsi[last] > 30) {
      score -= 3;
      signals.push({ type: 'sell', indicator: 'RSI', text: `RSI=${rsi[last]}，偏弱`, strength: 'weak' });
    }
  }
  
  // === KDJ信号 ===
  if (kdj.k[last] !== undefined) {
    if (kdj.k[last] < 20 && kdj.d[last] < 20) {
      score += 10;
      signals.push({ type: 'buy', indicator: 'KDJ', text: `KDJ超卖(K=${kdj.k[last]})`, strength: 'medium' });
    } else if (kdj.k[last] > 80 && kdj.d[last] > 80) {
      score -= 10;
      signals.push({ type: 'sell', indicator: 'KDJ', text: `KDJ超买(K=${kdj.k[last]})`, strength: 'medium' });
    }
    
    if (kdj.k[last] > kdj.d[last] && kdj.k[last - 1] <= kdj.d[last - 1]) {
      score += 10;
      signals.push({ type: 'buy', indicator: 'KDJ', text: 'KDJ金叉', strength: 'strong' });
    } else if (kdj.k[last] < kdj.d[last] && kdj.k[last - 1] >= kdj.d[last - 1]) {
      score -= 10;
      signals.push({ type: 'sell', indicator: 'KDJ', text: 'KDJ死叉', strength: 'strong' });
    }
  }
  
  // === 成交量信号 ===
  const avgVol5 = volumes.slice(-5).reduce((a, b) => a + b, 0) / 5;
  const avgVol20 = volumes.slice(-20).reduce((a, b) => a + b, 0) / 20;
  
  if (avgVol5 > avgVol20 * 1.5) {
    if (closes[last] > closes[last - 1]) {
      score += 8;
      signals.push({ type: 'buy', indicator: 'VOL', text: '放量上涨', strength: 'medium' });
    } else {
      score -= 8;
      signals.push({ type: 'sell', indicator: 'VOL', text: '放量下跌', strength: 'medium' });
    }
  } else if (avgVol5 < avgVol20 * 0.5) {
    signals.push({ type: 'neutral', indicator: 'VOL', text: '缩量，观望', strength: 'weak' });
  }
  
  // === 布林带信号 ===
  if (boll.upper[last] && boll.lower[last]) {
    if (price <= boll.lower[last]) {
      score += 8;
      signals.push({ type: 'buy', indicator: 'BOLL', text: '触及布林下轨，可能反弹', strength: 'medium' });
    } else if (price >= boll.upper[last]) {
      score -= 8;
      signals.push({ type: 'sell', indicator: 'BOLL', text: '触及布林上轨，注意回调', strength: 'medium' });
    }
  }
  
  // 限制分数范围
  score = Math.max(0, Math.min(100, score));
  
  // 生成建议
  let suggestion, level;
  if (score >= 75) {
    suggestion = '强烈建议买入';
    level = 'strong_buy';
  } else if (score >= 60) {
    suggestion = '建议买入';
    level = 'buy';
  } else if (score >= 45) {
    suggestion = '建议观望';
    level = 'hold';
  } else if (score >= 30) {
    suggestion = '建议卖出';
    level = 'sell';
  } else {
    suggestion = '强烈建议卖出';
    level = 'strong_sell';
  }
  
  return {
    score,
    suggestion,
    level,
    signals,
    indicators: {
      ma: { ma5: ma5[last], ma10: ma10[last], ma20: ma20[last], ma60: ma60[last] },
      macd: { dif: macd.dif[last], dea: macd.dea[last], macd: macd.macd[last] },
      rsi: rsi[last],
      kdj: { k: kdj.k[last], d: kdj.d[last], j: kdj.j[last] },
      boll: { upper: boll.upper[last], ma: boll.ma[last], lower: boll.lower[last] }
    },
    chartData: {
      dates: klines.map(k => k.date),
      opens: klines.map(k => k.open),
      closes: closes,
      highs: highs,
      lows: lows,
      volumes: volumes,
      ma5: ma5,
      ma10: ma10,
      ma20: ma20,
      ma60: ma60,
      macd: macd,
      rsi: rsi,
      kdj: kdj,
      boll: boll
    }
  };
}

// ===== HTTP服务 =====

const server = http.createServer(async (req, res) => {
  const parsedUrl = url.parse(req.url, true);
  const pathname = parsedUrl.pathname;
  
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET');
  
  // API路由
  if (pathname === '/api/analyze') {
    const code = parsedUrl.query.code;
    if (!code || !/^\d{6}$/.test(code)) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: '请输入6位股票代码' }));
      return;
    }
    
    try {
      const [quote, klines] = await Promise.all([
        getRealtimeQuote(code),
        getHistoryKline(code, 120)
      ]);
      
      if (!quote) {
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: '未找到该股票' }));
        return;
      }
      
      const analysis = analyzeStock(quote, klines);
      
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ quote, analysis }));
    } catch (e) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: '分析失败: ' + e.message }));
    }
    return;
  }
  
  // 热门股票
  if (pathname === '/api/hot') {
    const hotStocks = [
      { code: '600519', name: '贵州茅台' },
      { code: '000858', name: '五粮液' },
      { code: '601318', name: '中国平安' },
      { code: '600036', name: '招商银行' },
      { code: '000001', name: '平安银行' },
      { code: '601398', name: '工商银行' },
      { code: '600276', name: '恒瑞医药' },
      { code: '002594', name: '比亚迪' },
      { code: '300750', name: '宁德时代' },
      { code: '601899', name: '紫金矿业' }
    ];
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(hotStocks));
    return;
  }
  
  // 静态文件
  let filePath = pathname === '/' ? '/index.html' : pathname;
  filePath = path.join(__dirname, filePath);
  
  const ext = path.extname(filePath);
  const contentTypes = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.ico': 'image/x-icon'
  };
  
  try {
    const data = fs.readFileSync(filePath);
    res.writeHead(200, { 'Content-Type': contentTypes[ext] || 'text/plain' });
    res.end(data);
  } catch (e) {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not Found');
  }
});

server.listen(PORT, () => {
  console.log(`[Stock Analyzer] 运行在 http://localhost:${PORT}`);
});
