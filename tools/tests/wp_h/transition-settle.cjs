// Wait for what can still affect the paint, without pinning transition promises.
async function waitForCssTransitions(page, phase, deadlineMs = 5000) {
  const timeout = await page.evaluate(async deadlineMs => {
    function activeTransitions() {
      return document.getAnimations().filter(animation =>
        animation instanceof CSSTransition &&
        (animation.pending || (animation.playState !== 'idle' && animation.playState !== 'finished')));
    }

    function describe(animation) {
      const effect = animation.effect;
      const target = effect?.target;
      const timing = effect?.getComputedTiming();
      return {
        target: target?.tagName,
        id: target?.id,
        classes: target?.getAttribute('class'),
        connected: target?.isConnected,
        pseudo: effect?.pseudoElement,
        property: animation.transitionProperty,
        duration: timing?.duration,
        delay: timing?.delay,
        endTime: timing?.endTime,
        progress: timing?.progress,
        pending: animation.pending,
        playState: animation.playState,
        startTime: animation.startTime,
        currentTime: animation.currentTime,
        timelineTime: animation.timeline?.currentTime,
      };
    }

    const initial = activeTransitions();
    let frame, timer, quietFrames = 0;
    try {
      return await Promise.race([
        new Promise(resolve => {
          function poll() {
            quietFrames = activeTransitions().length ? 0 : quietFrames + 1;
            if (quietFrames === 2) resolve(null);
            else frame = requestAnimationFrame(poll);
          }
          frame = requestAnimationFrame(poll);
        }),
        new Promise(resolve => {
          timer = setTimeout(() => {
            const current = activeTransitions();
            resolve({
              visibility: document.visibilityState,
              documentTime: document.timeline.currentTime,
              quietFrames,
              initialCount: initial.length,
              activeCount: current.length,
              transitions: current.slice(0, 32).map(describe),
            });
          }, deadlineMs);
        }),
      ]);
    } finally {
      cancelAnimationFrame(frame);
      clearTimeout(timer);
    }
  }, deadlineMs);

  if (timeout) {
    throw new Error(`CSS transitions did not settle in ${deadlineMs}ms (${phase}): ${JSON.stringify(timeout)}`);
  }
}

module.exports = {waitForCssTransitions};
