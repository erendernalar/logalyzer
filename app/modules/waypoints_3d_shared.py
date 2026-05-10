"""Shared waypoint helpers for all 3-D views."""
import math


def waypoints_to_enu(waypoints, home_lat, home_lng, R=6_371_000.0):
    """Convert log waypoints to ENU scene coordinates (Three.js: x=East, y=Up, z=-North)."""
    result = []
    for wp in waypoints:
        wlat, wlng, walt = wp['lat'], wp['lng'], wp['alt']
        if wlat == 0.0 and wlng == 0.0:
            continue
        we = R * math.cos(math.radians(home_lat)) * math.radians(wlng - home_lng)
        wn = R * math.radians(wlat - home_lat)
        result.append({
            'x': round(we, 1), 'y': round(walt, 1), 'z': round(-wn, 1),
            'seq': wp['seq'], 'cmd': wp['cmd_id'],
        })
    return result


# JavaScript injected into every 3-D HTML template via the /*WAYPOINTS_JS*/ placeholder.
# Requires: WAYPOINTS constant and a <button id="bwp"> already in the page.
WAYPOINTS_JS = r"""
// ── Waypoints ─────────────────────────────────────────────────────────────
const WP_CMD_COLORS = {
  16: 0x58A6FF,  // NAV_WAYPOINT
  17: 0xF0883E,  // NAV_LOITER_UNLIM
  18: 0xF0883E,  // NAV_LOITER_TURNS
  19: 0xF85149,  // NAV_RETURN_TO_LAUNCH
  20: 0x3FB950,  // NAV_LAND
  21: 0xF0883E,  // NAV_LOITER_TIME
  22: 0xE3B341,  // NAV_TAKEOFF
};
function _wpColor(cmd){ return WP_CMD_COLORS[cmd] || 0x8B949E; }

function _wpSprite(seq, hexColor){
  const c = document.createElement('canvas');
  c.width = 80; c.height = 80;
  const ctx = c.getContext('2d');
  const hx = '#' + hexColor.toString(16).padStart(6,'0');
  ctx.shadowColor = hx; ctx.shadowBlur = 12;
  ctx.beginPath(); ctx.arc(40,40,30,0,Math.PI*2);
  ctx.fillStyle = 'rgba(13,17,23,0.85)'; ctx.fill();
  ctx.strokeStyle = hx; ctx.lineWidth = 4; ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.fillStyle = '#E6EDF3';
  ctx.font = 'bold 28px monospace';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(String(seq), 40, 41);
  const tex = new THREE.CanvasTexture(c);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({map:tex,transparent:true,depthTest:false}));
  sp.scale.set(14,14,1);
  return sp;
}

function _homeSprite(){
  const c = document.createElement('canvas');
  c.width = 80; c.height = 80;
  const ctx = c.getContext('2d');
  ctx.shadowColor = '#E3B341'; ctx.shadowBlur = 14;
  ctx.beginPath(); ctx.arc(40,40,30,0,Math.PI*2);
  ctx.fillStyle = 'rgba(13,17,23,0.85)'; ctx.fill();
  ctx.strokeStyle = '#E3B341'; ctx.lineWidth = 4; ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.fillStyle = '#E3B341';
  ctx.font = 'bold 28px monospace';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText('H', 40, 41);
  const tex = new THREE.CanvasTexture(c);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({map:tex,transparent:true,depthTest:false}));
  sp.scale.set(14,14,1);
  return sp;
}

function _labelSprite(text, color){
  const c = document.createElement('canvas');
  c.width = 120; c.height = 48;
  const ctx = c.getContext('2d');
  ctx.shadowColor = color; ctx.shadowBlur = 10;
  ctx.fillStyle = 'rgba(13,17,23,0.82)';
  ctx.roundRect(2, 2, 116, 44, 8);
  ctx.fill();
  ctx.strokeStyle = color; ctx.lineWidth = 3;
  ctx.roundRect(2, 2, 116, 44, 8);
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.fillStyle = color;
  ctx.font = 'bold 24px monospace';
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.fillText(text, 60, 25);
  const tex = new THREE.CanvasTexture(c);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({map:tex,transparent:true,depthTest:false}));
  sp.scale.set(20, 8, 1);
  return sp;
}

const wpGroup = new THREE.Group();
scene.add(wpGroup);

(function buildWaypoints(){
  const btn = document.getElementById('bwp');
  if(!WAYPOINTS || !WAYPOINTS.length){ if(btn) btn.style.display='none'; return; }
  const sorted = [...WAYPOINTS].sort((a,b)=>a.seq-b.seq);

  // Bold mission path tube (linewidth > 1 is unsupported in WebGL, so use tube geometry)
  const missionPts = sorted.map(w=>new THREE.Vector3(w.x, w.y, w.z));
  if(missionPts.length >= 2){
    const curve = new THREE.CatmullRomCurve3(missionPts, false, 'catmullrom', 0);
    const segments = missionPts.length * 8;
    const tubeGeom = new THREE.TubeGeometry(curve, segments, 0.8, 6, false);
    const tubeMat = new THREE.MeshBasicMaterial({color:0x58A6FF, opacity:0.75, transparent:true});
    wpGroup.add(new THREE.Mesh(tubeGeom, tubeMat));
  }

  sorted.forEach(function(wp){
    const col = _wpColor(wp.cmd);
    const poleH = Math.max(0.5, wp.y);
    const poleGeom = new THREE.CylinderGeometry(0.25, 0.25, poleH, 6);
    const poleMat = new THREE.MeshBasicMaterial({color:col, opacity:0.45, transparent:true});
    const pole = new THREE.Mesh(poleGeom, poleMat);
    pole.position.set(wp.x, poleH/2, wp.z);
    wpGroup.add(pole);
    const sphGeom = new THREE.SphereGeometry(2.8, 12, 8);
    const sphMat = new THREE.MeshPhongMaterial({
      color:col, emissive:col, emissiveIntensity:0.35, shininess:60
    });
    const sph = new THREE.Mesh(sphGeom, sphMat);
    sph.position.set(wp.x, wp.y, wp.z);
    wpGroup.add(sph);
    const sprite = _wpSprite(wp.seq, col);
    sprite.position.set(wp.x, wp.y + 9, wp.z);
    wpGroup.add(sprite);

    // Takeoff: upward arrow cone above the sphere
    if(wp.cmd === 22){
      const arrowGeom = new THREE.ConeGeometry(2.2, 7, 8);
      const arrowMat = new THREE.MeshPhongMaterial({color:0xE3B341, emissive:0xE3B341, emissiveIntensity:0.4});
      const arrow = new THREE.Mesh(arrowGeom, arrowMat);
      arrow.position.set(wp.x, wp.y + 7, wp.z);
      wpGroup.add(arrow);
      const labelSprite = _labelSprite('TKOF', '#E3B341');
      labelSprite.position.set(wp.x, wp.y + 18, wp.z);
      wpGroup.add(labelSprite);
    }

    // Land: ground target rings + downward arrow cone
    if(wp.cmd === 20){
      for(const r of [[2.5, 4], [6, 7.5]]){
        const ringGeom = new THREE.RingGeometry(r[0], r[1], 32);
        ringGeom.rotateX(-Math.PI / 2);
        const ring = new THREE.Mesh(ringGeom,
          new THREE.MeshBasicMaterial({color:0x3FB950, side:THREE.DoubleSide, opacity:0.85, transparent:true}));
        ring.position.set(wp.x, 0.1, wp.z);
        wpGroup.add(ring);
      }
      const arrowGeom = new THREE.ConeGeometry(2.2, 7, 8);
      const arrowMat = new THREE.MeshPhongMaterial({color:0x3FB950, emissive:0x3FB950, emissiveIntensity:0.4});
      const arrow = new THREE.Mesh(arrowGeom, arrowMat);
      arrow.rotation.z = Math.PI;
      arrow.position.set(wp.x, wp.y + 7, wp.z);
      wpGroup.add(arrow);
      const labelSprite = _labelSprite('LAND', '#3FB950');
      labelSprite.position.set(wp.x, wp.y + 18, wp.z);
      wpGroup.add(labelSprite);
    }
  });

  // Home marker at ENU origin (0, 0, 0)
  const homeRingGeom = new THREE.RingGeometry(3.5, 5.5, 32);
  homeRingGeom.rotateX(-Math.PI / 2);
  const homeRingMat = new THREE.MeshBasicMaterial({color:0xE3B341, side:THREE.DoubleSide, opacity:0.9, transparent:true});
  wpGroup.add(new THREE.Mesh(homeRingGeom, homeRingMat));
  const homeSprite = _homeSprite();
  homeSprite.position.set(0, 9, 0);
  wpGroup.add(homeSprite);
})();

let _wpVisible = true;
function toggleWaypoints(){
  _wpVisible = !_wpVisible;
  wpGroup.visible = _wpVisible;
  const btn = document.getElementById('bwp');
  if(btn) btn.classList.toggle('on', _wpVisible);
}
"""
