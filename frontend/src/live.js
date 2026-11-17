export class LiveEffects {
  constructor(stage, onError) {
    this.stage = stage; this.onError = onError;
    this.lastAction = null; this.pending = []; this.musicSeq = null;
    this.music = document.querySelector('#music');
    this.music.addEventListener('timeupdate', () => this.lyric());
  }

  setAvatar(avatar) {
    if (this.player) this.player.setModel(avatar, document.querySelector('#stage'));
    else this.player = new window.GiftPlayer({ model: avatar, stage: document.querySelector('#stage'),
      onFinish: () => this.pump() });
  }

  reset() { this.player?.stop(); this.pending = []; this.lastAction = null; this.musicSeq = null; this.music.pause(); }

  ingest(state) {
    if (this.lastAction === null) this.lastAction = state.action_seq || 0;
    for (const action of state.actions || []) {
      if (action.seq <= this.lastAction) continue;
      this.lastAction = action.seq;
      if (action.interrupt) { this.pending = []; this.player?.stop(); }
      this.pending.push(action);
      this.pending = this.pending.slice(-5);
    }
    this.pump();
    const song = state.now_playing;
    if (song && song.seq !== this.musicSeq) { this.musicSeq = song.seq; this.playMusic(song); }
    const board = document.querySelector('#gift-board');
    board.replaceChildren(...(state.gift_board || []).map(([nick, value]) => {
      const row = document.createElement('li'); row.textContent = `${nick} · ${value}`; return row;
    }));
  }

  pump() {
    if (!this.player || this.player.isActive() || !this.pending.length) return;
    this.player.play(this.pending.shift());
  }

  playMusic(song) {
    this.music.pause(); this.lines = song.lyric || [];
    document.querySelector('#music-info').hidden = !song.url;
    document.querySelector('#song-name').textContent = [song.name, song.artist].filter(Boolean).join(' · ');
    if (!song.url) { this.music.removeAttribute('src'); return; }
    const url = new URL(song.url, location.origin);
    if (!['http:', 'https:'].includes(url.protocol)) return;
    this.music.src = url.href;
    if (this.unlocked) this.music.play().catch(() => this.onError('歌曲未能自动播放，请使用音乐控件'));
  }

  unlock() {
    this.unlocked = true;
    if (this.music.getAttribute('src')) this.music.play().catch(() => this.onError('歌曲播放失败'));
  }

  lyric() {
    const line = (this.lines || []).filter(line => line.t <= this.music.currentTime).at(-1);
    document.querySelector('#lyric').textContent = line?.text || '';
  }
}
