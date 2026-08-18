const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const API_KEY = process.env.YOUTUBE_API_KEY;
const CHANNEL_ID = process.env.YOUTUBE_CHANNEL_ID;
const YT_BASE = 'https://www.googleapis.com/youtube/v3';

async function ytJSON(endpoint, params) {
  const qs = new URLSearchParams({ ...params, key: API_KEY }).toString();
  const res = await fetch(`${YT_BASE}/${endpoint}?${qs}`);
  if (!res.ok) throw new Error(`YouTube API ${endpoint}: ${res.status}`);
  return res.json();
}

async function fetchYouTube() {
  const ch = await ytJSON('channels', { part: 'statistics', id: CHANNEL_ID });
  if (!ch.items?.length) throw new Error('Channel not found: ' + CHANNEL_ID);
  const s = ch.items[0].statistics;

  const search = await ytJSON('search', {
    part: 'snippet', channelId: CHANNEL_ID,
    type: 'video', order: 'date', maxResults: 50,
  });
  const ids = search.items.map(v => v.id.videoId).filter(Boolean).join(',');
  if (!ids) return { subscribers: +s.subscriberCount, totalViews: +s.viewCount, views30d: 0, likes30d: 0, comments30d: 0, avgDuration: '--', topVideos: [] };

  const vids = await ytJSON('videos', { part: 'statistics,contentDetails,snippet', id: ids });

  const cutoff = Date.now() - 30 * 86400000;
  const recent = vids.items.filter(v => new Date(v.snippet.publishedAt) >= cutoff);

  const sum = (arr, key) => arr.reduce((t, v) => t + parseInt(v.statistics[key] || 0), 0);
  const views30d = sum(recent, 'viewCount');
  const likes30d = sum(recent, 'likeCount');
  const comments30d = sum(recent, 'commentCount');

  function parseSec(iso) {
    const m = iso.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/);
    return (+(m?.[1] || 0)) * 3600 + (+(m?.[2] || 0)) * 60 + (+(m?.[3] || 0));
  }
  const durs = recent.map(v => parseSec(v.contentDetails.duration)).filter(d => d > 0);
  const avgSec = durs.length ? Math.round(durs.reduce((a, b) => a + b) / durs.length) : 0;
  const avgDuration = `${Math.floor(avgSec / 60)}:${String(avgSec % 60).padStart(2, '0')}`;

  const topVideos = vids.items
    .sort((a, b) => +b.statistics.viewCount - +a.statistics.viewCount)
    .slice(0, 10)
    .map(v => ({
      title: v.snippet.title,
      date: new Date(v.snippet.publishedAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
      views: +v.statistics.viewCount,
    }));

  return { subscribers: +s.subscriberCount, totalViews: +s.viewCount, views30d, likes30d, comments30d, avgDuration, topVideos };
}

function fmtBig(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  return n.toLocaleString('en-US');
}

async function main() {
  const manual = JSON.parse(fs.readFileSync(path.join(ROOT, 'manual-stats.json'), 'utf-8'));

  let existing = { monthly: [] };
  const dataPath = path.join(ROOT, 'data.json');
  if (fs.existsSync(dataPath)) existing = JSON.parse(fs.readFileSync(dataPath, 'utf-8'));

  let yt = null;
  if (API_KEY && CHANNEL_ID) {
    yt = await fetchYouTube();
    console.log(`YouTube: ${yt.subscribers} subs, ${yt.views30d} views (30d)`);
  } else {
    console.log('YOUTUBE_API_KEY or YOUTUBE_CHANNEL_ID not set — skipping YouTube');
  }

  const ap = manual.apple || {};
  const sp = manual.spotify || {};

  const totalFollowers = (yt?.subscribers || 0) + (ap.followers || 0) + (sp.followers || 0);
  const monthlyDownloads = (yt?.views30d || 0) + (ap.downloads30d || 0) + (sp.streams30d || 0);

  // Upsert current month in history
  const monthly = existing.monthly || [];
  const now = new Date();
  const monthKey = now.toLocaleString('en-US', { month: 'short' }) + " '" + String(now.getFullYear()).slice(-2);
  const entry = { month: monthKey, yt: yt?.views30d || 0, ap: ap.downloads30d || 0, sp: sp.streams30d || 0 };
  const idx = monthly.findIndex(m => m.month === monthKey);
  if (idx >= 0) monthly[idx] = entry; else monthly.push(entry);
  while (monthly.length > 12) monthly.shift();

  // Merge top episodes
  const episodes = [];
  if (yt?.topVideos) yt.topVideos.forEach(v => episodes.push({ title: v.title, date: v.date, plays: v.views }));
  if (manual.topEpisodes) manual.topEpisodes.forEach(e => episodes.push(e));
  episodes.sort((a, b) => (b.plays || 0) - (a.plays || 0));
  const top5 = episodes.slice(0, 5).map(e => ({
    title: e.title, date: e.date,
    plays: typeof e.plays === 'number' ? (e.plays >= 1e3 ? (e.plays / 1e3).toFixed(1) + 'K' : String(e.plays)) : e.plays,
  }));

  const data = {
    updatedAt: now.toISOString(),
    kpis: {
      followers: { value: totalFollowers },
      downloads: { value: monthlyDownloads },
      episodes: { value: manual.totalEpisodes || 0 },
      rating: { value: ap.rating || 0 },
    },
    youtube: yt ? [
      ['Subscribers', fmtBig(yt.subscribers)],
      ['Total views', fmtBig(yt.totalViews)],
      ['Views (30d)', fmtBig(yt.views30d)],
      ['Avg. duration', yt.avgDuration],
      ['Likes (30d)', fmtBig(yt.likes30d)],
      ['Comments (30d)', fmtBig(yt.comments30d)],
    ] : [],
    apple: [
      ['Followers', fmtBig(ap.followers || 0)],
      ['Rating', ap.rating || '--'],
      ['Written reviews', fmtBig(ap.reviews || 0)],
      ['Downloads (30d)', fmtBig(ap.downloads30d || 0)],
      ['Avg. completion', ap.avgCompletion || '--'],
      ['Top country', ap.topCountry || '--'],
    ],
    spotify: [
      ['Followers', fmtBig(sp.followers || 0)],
      ['Streams (30d)', fmtBig(sp.streams30d || 0)],
      ['Avg. listen time', sp.avgListenTime || '--'],
      ['Starts (30d)', fmtBig(sp.starts30d || 0)],
      ['Saves', fmtBig(sp.saves || 0)],
      ['Top market', sp.topMarket || '--'],
    ],
    monthly,
    topEpisodes: top5,
  };

  fs.writeFileSync(dataPath, JSON.stringify(data, null, 2));
  console.log('Wrote data.json');
}

main().catch(e => { console.error(e); process.exit(1); });
