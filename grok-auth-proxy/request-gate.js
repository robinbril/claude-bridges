'use strict';

function createGate(max, queueLimit, waitMs) {
  let active = 0;
  const queue = [];

  function pump() {
    while (active < max && queue.length) {
      const item = queue.shift();
      clearTimeout(item.timer);
      item.started = true;
      active++;
      item.run(() => {
        if (item.released) return;
        item.released = true;
        active--;
        pump();
      });
    }
  }

  function acquire(run, reject) {
    if (active >= max && queue.length >= queueLimit) {
      reject('queue_full');
      return () => {};
    }
    const item = { run, started: false, released: false, timer: null };
    const cancel = () => {
      if (item.started) return;
      clearTimeout(item.timer);
      const index = queue.indexOf(item);
      if (index >= 0) queue.splice(index, 1);
    };
    item.timer = setTimeout(() => {
      cancel();
      reject('queue_timeout');
    }, waitMs);
    queue.push(item);
    pump();
    return cancel;
  }

  return { acquire, snapshot: () => ({ active, queued: queue.length, max }) };
}

module.exports = { createGate };
