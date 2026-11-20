"""로봇 얼굴 화면보호기 (Robot screensaver) — 주행 화면 대체 모드.

검은 화면에 큰 네모 눈 두 개만, 입 없음. 눈은 크림빛에서 살구빛으로
번지는 그라데이션에 유리 같은 반짝임이 있고, 화면 테두리에 상태별
빛띠가 돈다. 각 표정은 주행 상태에 따라 바뀌며 (모찌와 같은 트리거),
자동 순환하지 않는다.

동작 (OnroadMochiMode=1일 때만, 주행 화면에서만):
- 얼굴을 켜면 주행 화면이 뜰 때 자동으로 로봇 화면으로 전환 (자동 시작).
- 로봇 화면을 터치하면 주행 화면으로 복귀 (이번 운전에서는 자동 시작 안 함).
- 주행 화면을 650ms 길게 누르면 로봇 화면으로 전환 (게이지 표시).
- 주행 화면 짧은 터치는 아무 동작 없음 (주행 중 오동작 방지).

표정 트리거 (우선순위 순, 모찌와 동일):
  긴급제동(aEgo <= -4) > 급감속(aEgo <= -2.5) > 급가속(aEgo >= 2)
  > 후진(R) > 신호대기(빨간불 + 정지) > 오토홀드
  > 인게이지 / 인게이지 해제 / 정지 중 출발 (전환 직후 잠깐)
  > 차선변경(desire) > 깜빡이 > 정지 중 수면(정지 + 미인게이지) > 주행중(기본)

얼굴 선택: OnroadFaceMode=1일 때 이 로봇 얼굴이 나오고, 0이면 모찌가 나온다.
마스터 스위치는 OnroadMochiMode (0이면 둘 다 안 나옴).

참고 — 미리보기 영상에 있던 '클램프 보정'과 '운전자 주의' 표정은
이 코드에 트리거가 없어 따로 넣지 않았다. 클램프 보정의 조건으로
알려진 controlsState.clampSteerActive는 이 포크의 메시지 정의에 없고,
운전자 주의는 임계값을 새로 정해야 해서, 모찌와 같은 트리거만 쓴다.
"""

import math
import time

import pyray as rl

from openpilot.cereal import car, log
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, MouseEvent, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached

LONG_PRESS_S = 0.65  # 주행 화면 길게 누르기 시간 (초)
TRANSIENT_S = 2.5  # 인게이지/해제 표정 유지 시간 (초)
START_S = 3.0  # 정지 중 출발 표정 유지 시간 (초)
HOLD_SHOW_S = 30.0  # 오토홀드 표정 최대 유지 시간 (초)

EMERGENCY_DECEL = -4.0  # m/s^2, 긴급제동
DECEL = -2.5  # m/s^2, 급감속
ACCEL = 2.0  # m/s^2, 급가속
STOP_SPEED = 0.5  # m/s, 정지 판정
HOLD_SPEED = 0.05  # m/s, 오토홀드 정지 판정

# 표정 이름 (모찌와 동일)
EXPR_DRIVING = "driving"  # 주행중
EXPR_DECEL = "decel"  # 급감속
EXPR_EMERGENCY = "emergency"  # 긴급제동
EXPR_ACCEL = "accel"  # 급가속
EXPR_LANE_LEFT = "lane_left"  # 좌측 차선변경
EXPR_LANE_RIGHT = "lane_right"  # 우측 차선변경
EXPR_BLINK_LEFT = "blink_left"  # 좌측 깜빡이
EXPR_BLINK_RIGHT = "blink_right"  # 우측 깜빡이
EXPR_START = "start"  # 정지 중 출발
EXPR_ENGAGE = "engage"  # 인게이지
EXPR_DISENGAGE = "disengage"  # 인게이지 해제
EXPR_REVERSE = "reverse"  # 후진
EXPR_TRAFFIC = "traffic"  # 신호대기
EXPR_AUTOHOLD = "autohold"  # 오토홀드
EXPR_SLEEP = "sleep"  # 정지 중 수면 (정지 + 미인게이지)

EYE_WHITE = rl.Color(255, 255, 255, 255)
C_DARK = rl.Color(40, 40, 45, 255)
C_GAUGE_BG = rl.Color(255, 255, 255, 60)
C_GAUGE_FG = rl.Color(0, 220, 180, 255)
C_ZZZ = rl.Color(150, 200, 255, 255)

# 표정별 눈 색 (위쪽 그라데이션 색, 아래쪽 색, 테두리 빛 색)
EYE_COLORS = {
  EXPR_DRIVING: ((255, 246, 225), (255, 165, 70), (60, 220, 140)),
  EXPR_START: ((210, 255, 225), (70, 225, 140), (60, 220, 140)),
  EXPR_ENGAGE: ((210, 255, 225), (70, 225, 140), (60, 220, 140)),
  EXPR_ACCEL: ((210, 255, 225), (70, 225, 140), (60, 220, 140)),
  EXPR_LANE_LEFT: ((200, 230, 255), (80, 160, 255), (80, 160, 255)),
  EXPR_LANE_RIGHT: ((200, 230, 255), (80, 160, 255), (80, 160, 255)),
  EXPR_BLINK_LEFT: ((255, 245, 180), (255, 210, 60), (255, 210, 60)),
  EXPR_BLINK_RIGHT: ((255, 245, 180), (255, 210, 60), (255, 210, 60)),
  EXPR_EMERGENCY: ((255, 200, 210), (255, 90, 120), (255, 90, 120)),
  EXPR_DECEL: ((255, 220, 180), (255, 110, 60), (255, 140, 60)),
  EXPR_REVERSE: ((200, 230, 255), (80, 160, 255), (80, 160, 255)),
  EXPR_TRAFFIC: ((255, 240, 200), (255, 170, 80), (255, 170, 80)),
  EXPR_AUTOHOLD: ((255, 246, 225), (255, 165, 70), (60, 220, 140)),
  EXPR_DISENGAGE: ((225, 225, 230), (135, 135, 145), (150, 150, 160)),
  EXPR_SLEEP: ((0, 0, 0), (0, 0, 0), (90, 140, 200)),
}


def _col(rgb: tuple[int, int, int], alpha: int = 255) -> rl.Color:
  return rl.Color(rgb[0], rgb[1], rgb[2], max(0, min(255, alpha)))


class RobotSaver:
  def __init__(self):
    self.active = False  # 로봇 화면 표시 중
    self._pressing = False  # 주행 화면 길게 누르는 중
    self._press_start = 0.0
    self._press_x = 0.0
    self._press_y = 0.0
    self._press_fired = False

    self._auto_started = False  # 자동 시작(설정 ON 후 최초 1회) 여부
    self._dismissed = False  # 사용자가 로봇을 터치해 끈 뒤인지

    self._expr = EXPR_DRIVING
    self._prev_engaged = False
    self._prev_v_ego = 0.0
    self._prev_brake = False
    self._transient = ("", 0.0)  # (표정, 유지 종료 시각)
    self._brake_held_stop = False
    self._hold_until = 0.0

    self._v_ego = 0.0  # 현재 속도 (m/s, 미니 HUD용)
    self._v_target = 0.0  # 크루즈 설정 속도 (m/s, 미니 HUD용)

    self._font_bold: rl.Font | None = None
    self._font_medium: rl.Font | None = None

  # ---------------------------------------------------------------
  # 외부 연결: AugmentedRoadView에서 호출
  # ---------------------------------------------------------------

  @property
  def enabled(self) -> bool:
    return ui_state.onroad_mochi

  def handle_mouse_event(self, ev: MouseEvent) -> None:
    """주행 화면 터치 처리. 짧은 터치는 무시, 길게 누르기만 인식."""
    if not self.enabled or not ui_state.started:
      self._cancel_press()
      return

    if self.active:
      # 로봇 화면: 터치하면 바로 주행 화면으로 복귀
      if ev.left_pressed:
        self.active = False
        self._dismissed = True  # 끈 뒤에는 이번 운전에서 자동 시작 안 함
        self._cancel_press()
      return

    # 주행 화면: 길게 누르기만 인식 (짧은 터치는 오동작 방지용 무동작)
    if ev.left_pressed:
      self._pressing = True
      self._press_start = time.monotonic()
      self._press_x = ev.pos.x
      self._press_y = ev.pos.y
      self._press_fired = False
    elif ev.left_released:
      self._cancel_press()

  def update(self) -> None:
    """매 프레임 호출: 자동 시작 + 길게 누르기 판정 + 표정 선택."""
    if not self.enabled or not ui_state.started:
      self.active = False
      self._auto_started = False  # 다음 운전 때 다시 자동 시작되도록
      self._dismissed = False
      self._cancel_press()
      return

    now = time.monotonic()

    # 자동 시작: 얼굴을 켜는 즉시(설정 ON + 주행 화면) 1회 자동 표시.
    # 사용자가 터치해 껐다면 이번 운전에서는 다시 안 뜬다.
    if not self.active and not self._auto_started and not self._dismissed:
      self.active = True
      self._auto_started = True
      self._pressing = False

    # 길게 누르기: 손가락을 가만히 들고 있어도 중간 이벤트가 안 오므로
    # 시간으로 직접 판정한다. 손을 뗐으면 release 이벤트가 취소한다.
    if self._pressing and not self._press_fired:
      if not gui_app.last_mouse_event.left_down:
        self._cancel_press()
      elif now - self._press_start >= LONG_PRESS_S:
        self.active = True
        self._press_fired = True
        self._pressing = False

    self._select_expression(now)

  def render(self, rect: rl.Rectangle) -> None:
    """로봇 화면 또는 길게 누르기 게이지 그리기."""
    if not self.enabled or not ui_state.started:
      return

    if self._pressing and not self.active:
      progress = min(1.0, (time.monotonic() - self._press_start) / LONG_PRESS_S)
      self._draw_long_press_gauge(self._press_x, self._press_y, progress)
      return

    if not self.active:
      return

    # 검은 화면 전체를 덮는다 (주행 화면 대체)
    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height), rl.BLACK)
    self._draw_face(rect, rl.get_time())

  # ---------------------------------------------------------------
  # 표정 선택 (모찌와 동일한 트리거·우선순위 + 정지 중 수면)
  # ---------------------------------------------------------------

  def _select_expression(self, now: float) -> None:
    sm = ui_state.sm
    cs = sm["carState"]
    lp = sm["lateralPlan"]
    lon = sm["longitudinalPlan"]
    try:
      v_ego = float(cs.vEgo)
      a_ego = float(cs.aEgo)
    except Exception:
      v_ego, a_ego = 0.0, 0.0
    self._v_ego = v_ego
    try:
      self._v_target = float(cs.cruiseState.speed)
    except Exception:
      self._v_target = 0.0

    engaged = ui_state.engaged

    # 인게이지 / 해제 전환 직후 잠깐 보여주는 표정
    if engaged != self._prev_engaged:
      self._transient = ("engage" if engaged else "disengage", now + TRANSIENT_S)
      self._prev_engaged = engaged
    # 정지에서 출발 직후 잠깐 보여주는 표정
    if self._prev_v_ego < 0.3 and v_ego > STOP_SPEED:
      self._transient = ("start", now + START_S)
    self._prev_v_ego = v_ego

    # 오토홀드 근사 감지: 정지 + D/low + 미인게이지 + 크루즈 꺼짐에서
    # 브레이크를 밟았다 뗀 뒤 (실제 홀드 상태는 UI 메시지에 없음)
    try:
      brake = bool(cs.brakePressed)
      gas = bool(cs.gasPressed)
      standstill = bool(cs.standstill)
      gear = cs.gearShifter
      cruise_on = bool(cs.cruiseState.enabled)
    except Exception:
      brake, gas, standstill, gear, cruise_on = False, False, False, None, False
    hold_ok = (standstill and abs(v_ego) < HOLD_SPEED
               and gear in (car.CarState.GearShifter.drive, car.CarState.GearShifter.low)
               and not engaged and not cruise_on)
    if brake and hold_ok:
      self._brake_held_stop = True
    if self._brake_held_stop and not brake and hold_ok:
      self._hold_until = now + HOLD_SHOW_S
      self._brake_held_stop = False
    if not hold_ok or gas:
      self._hold_until = 0.0
      if not hold_ok:
        self._brake_held_stop = False
    self._prev_brake = brake

    try:
      desire = lp.desire
    except Exception:
      desire = log.Desire.none
    try:
      traffic_red = int(lon.trafficState) == 1
    except Exception:
      traffic_red = False
    try:
      left_blinker = bool(cs.leftBlinker)
      right_blinker = bool(cs.rightBlinker)
    except Exception:
      left_blinker, right_blinker = False, False

    # 우선순위 순으로 표정 결정 (모찌와 동일, 수면은 정지 + 미인게이지일 때만)
    if a_ego <= EMERGENCY_DECEL:
      expr = EXPR_EMERGENCY
    elif a_ego <= DECEL:
      expr = EXPR_DECEL
    elif a_ego >= ACCEL:
      expr = EXPR_ACCEL
    elif gear == car.CarState.GearShifter.reverse:
      expr = EXPR_REVERSE
    elif traffic_red and v_ego < STOP_SPEED:
      expr = EXPR_TRAFFIC
    elif now < self._hold_until:
      expr = EXPR_AUTOHOLD
    elif self._transient[0] and now < self._transient[1]:
      expr = self._transient[0]
    elif desire == log.Desire.laneChangeLeft:
      expr = EXPR_LANE_LEFT
    elif desire == log.Desire.laneChangeRight:
      expr = EXPR_LANE_RIGHT
    elif left_blinker:
      expr = EXPR_BLINK_LEFT
    elif right_blinker:
      expr = EXPR_BLINK_RIGHT
    elif standstill and not engaged:
      expr = EXPR_SLEEP
    else:
      expr = EXPR_DRIVING
    self._expr = expr

  def _cancel_press(self) -> None:
    self._pressing = False
    self._press_fired = False

  # ---------------------------------------------------------------
  # 그리기: 얼굴
  # ---------------------------------------------------------------

  def _fonts(self):
    if self._font_bold is None:
      self._font_bold = gui_app.font(FontWeight.BOLD)
      self._font_medium = gui_app.font(FontWeight.MEDIUM)
    return self._font_bold, self._font_medium

  def _draw_long_press_gauge(self, x: float, y: float, progress: float) -> None:
    """길게 누르는 동안 손가락 위치에 차오르는 원형 게이지."""
    r = 70
    rl.draw_ring(rl.Vector2(x, y), r - 8, r, 0.0, 360.0, 48, C_GAUGE_BG)
    if progress > 0.0:
      rl.draw_ring(rl.Vector2(x, y), r - 8, r, -90.0, -90.0 + 360.0 * progress, 48, C_GAUGE_FG)
    rl.draw_circle(int(x), int(y), int(10 + 26 * progress), C_GAUGE_FG)

  def _blink_scale(self, t: float, period: float = 2.3) -> float:
    """깜빡임: 2.3초 주기로 0.12초간 감았다 뜬다. 1=활짝."""
    p = (t % period) / period
    if p < 0.12:
      return 0.12 + 0.88 * abs(p - 0.06) / 0.06
    return 1.0

  def _draw_face(self, rect: rl.Rectangle, t: float) -> None:
    s = min(rect.width / 2160.0, rect.height / 1080.0)
    cx = rect.x + rect.width / 2.0
    cy = rect.y + rect.height / 2.0
    expr = self._expr
    top_rgb, bottom_rgb, rim_rgb = EYE_COLORS.get(expr, EYE_COLORS[EXPR_DRIVING])

    # 표정별 전체 움직임 (떨림 / 숨쉬기 / 시선 이동)
    jx = jy = 0.0
    breathe = 1.0
    gaze_dx = gaze_dy = 0.0
    if expr == EXPR_EMERGENCY:
      jx = 8.0 * s * math.sin(t * math.pi * 2.0 * 13.0)
      jy = 6.0 * s * math.sin(t * math.pi * 2.0 * 17.0)
    elif expr == EXPR_DECEL:
      jx = 5.0 * s * math.sin(t * math.pi * 2.0 * 9.0)
    elif expr == EXPR_AUTOHOLD:
      breathe = 1.0 + 0.02 * math.sin(t * math.pi * 2.0 / 4.0)
    elif expr == EXPR_DRIVING:
      breathe = 1.0 + 0.02 * math.sin(t * math.pi * 2.0 / 4.0)
      gaze_dx = 18.0 * s * math.sin(t * math.pi * 2.0 / 9.0)
      gaze_dy = 10.0 * s * math.sin(t * math.pi * 2.0 / 13.0 + 1.0)

    # 화면 테두리 빛띠 (인게이지 때는 숨 쉬듯 밝아졌다 어두워짐)
    rim_alpha = 255
    if expr == EXPR_ENGAGE:
      rim_alpha = int(150 + 100 * math.sin(t * math.pi * 2.0 / 2.0))
    elif expr == EXPR_SLEEP:
      rim_alpha = 140
    rim_rect = rl.Rectangle(rect.x + 36.0 * s, rect.y + 36.0 * s,
                            rect.width - 72.0 * s, rect.height - 72.0 * s)
    rim_round = min(1.0, 2.0 * 60.0 * s / min(rim_rect.width, rim_rect.height))
    rl.draw_rectangle_rounded_lines_ex(rim_rect, rim_round, 12, 16, _col(rim_rgb, 70))
    rl.draw_rectangle_rounded_lines_ex(rim_rect, rim_round, 12, 5, _col(rim_rgb, rim_alpha))

    if expr == EXPR_SLEEP:
      # 수면: 눈은 감고 "Zzz"만 천천히 떠오름
      self._draw_zzz(cx, cy, s, t)
      return

    # 눈 크기·위치
    eye_scale = 1.0
    if expr == EXPR_EMERGENCY:
      eye_scale = 1.08
    elif expr == EXPR_ACCEL:
      eye_scale = 1.05
    eye_w = 340.0 * s * breathe * eye_scale
    eye_h = 380.0 * s * breathe * eye_scale
    eye_dx = 280.0 * s * breathe
    eye_y = -60.0 * s * breathe
    eye_r = 95.0 * s * breathe

    # 표정별 눈동자(눈 전체) 위치·눈꺼풀
    lid = 1.0  # 1=활짝, 0.5=반쯤 감음
    arch = False  # 해맑은 아치 눈 (출발)
    if expr == EXPR_LANE_LEFT or expr == EXPR_BLINK_LEFT:
      gaze_dx = -40.0 * s if expr == EXPR_LANE_LEFT else -25.0 * s
    elif expr == EXPR_LANE_RIGHT or expr == EXPR_BLINK_RIGHT:
      gaze_dx = 40.0 * s if expr == EXPR_LANE_RIGHT else 25.0 * s
    elif expr == EXPR_REVERSE:
      gaze_dy = -25.0 * s
    elif expr == EXPR_TRAFFIC:
      lid = 0.5
    elif expr == EXPR_DISENGAGE:
      gaze_dy = 25.0 * s
      lid = 0.55
    elif expr == EXPR_AUTOHOLD:
      lid = 0.8
    elif expr == EXPR_DECEL:
      lid = 0.8
    elif expr == EXPR_START:
      arch = True

    blink = self._blink_scale(t)
    lid = min(lid, blink) if expr not in (EXPR_TRAFFIC, EXPR_DISENGAGE) else lid

    # 눈 (좌·우)
    for side in (-1, 1):
      ex = cx + side * eye_dx + jx + gaze_dx
      ey = cy + eye_y + jy + gaze_dy
      wink_closed = False
      if expr in (EXPR_BLINK_LEFT, EXPR_BLINK_RIGHT):
        wink_side = -1 if expr == EXPR_BLINK_LEFT else 1
        if side == wink_side and (int(t * 2.0) % 2 == 0):
          wink_closed = True
      self._draw_eye(ex, ey, eye_w, eye_h, eye_r,
                     _col(top_rgb), _col(bottom_rgb),
                     lid, arch, wink_closed, s)

    # 좌·우 방향 표시 (눈 바깥쪽 < / >)
    self._draw_direction_chevron(cx, cy, s, t, expr)

    # 주행중: 눈가에 작은 별 반짝임이 드물게 떴다 사라짐
    if expr == EXPR_DRIVING:
      star_a = int(90 + 90 * math.sin(t * math.pi * 2.0 / 9.0))
      if star_a > 0:
        rl.draw_circle(int(cx + 580.0 * s), int(cy - 300.0 * s),
                       int(8.0 * s), rl.Color(255, 255, 255, star_a))

    # 인게이지: 아래쪽 미니 HUD (핸들 아이콘 + 현재 속도 + 목표 속도)
    if expr == EXPR_ENGAGE:
      self._draw_mini_hud(cx, cy + 330.0 * s, s, _col(rim_rgb))

  def _draw_eye(self, ex: float, ey: float, w: float, h: float, r: float,
                top: rl.Color, bottom: rl.Color,
                lid: float, arch: bool, wink_closed: bool, s: float) -> None:
    # 둥근 네모 눈 (아래쪽 색으로 바탕)
    roundness = min(1.0, 2.0 * r / min(w, h))
    rl.draw_rectangle_rounded(rl.Rectangle(ex - w / 2, ey - h / 2, w, h),
                              roundness, 12, bottom)
    # 크림빛 → 살구빛 그라데이션 (안쪽으로 들여서 둥근 모서리 안에 맞춤)
    d = 0.30 * r
    rl.draw_rectangle_gradient_v(int(ex - w / 2 + d), int(ey - h / 2 + d),
                                 int(w - 2 * d), int(h - 2 * d), top, bottom)
    # 유리 하이라이트
    rl.draw_circle(int(ex - w * 0.22), int(ey - h * 0.28), int(30 * s),
                   rl.Color(255, 255, 255, 220))
    rl.draw_circle(int(ex - w * 0.30), int(ey - h * 0.36), int(10 * s),
                   rl.Color(255, 255, 255, 255))
    # 아래쪽 가운데 작은 홈 (영상 속 눈 아래 패임)
    rl.draw_circle(int(ex), int(ey + h / 2 - 4 * s), int(24 * s), rl.BLACK)
    # 눈꺼풀: 위에서 아래로 감김 (배경이 검은색이라 모서리가 보여도 티 안 남)
    if lid < 1.0:
      cover_h = h * (1.0 - lid)
      rl.draw_rectangle(int(ex - w / 2), int(ey - h / 2), int(w), int(cover_h), rl.BLACK)
    # 해맑은 아치 눈 (출발): 아래쪽을 덮어 돔 모양만 남김
    if arch:
      rl.draw_rectangle(int(ex - w / 2), int(ey + h * 0.05), int(w), int(h * 0.45), rl.BLACK)
    # 윙크: 깜빡이는 쪽 눈을 번갈아 감음
    if wink_closed:
      rl.draw_rectangle(int(ex - w / 2), int(ey - h / 2), int(w), int(h), rl.BLACK)

  def _draw_direction_chevron(self, cx: float, cy: float, s: float, t: float, expr: str) -> None:
    """좌·우 방향 표시: 눈 바깥쪽에 < / > 를 두 줄 선으로 그린다.
    깜빡이는 해당 쪽 눈 윙크와 같은 0.5초 리듬으로 점멸하고,
    차선변경은 바깥쪽으로 살짝 밀려났다 돌아오는 모션을 준다."""
    if expr == EXPR_LANE_LEFT or expr == EXPR_BLINK_LEFT:
      side = -1
    elif expr == EXPR_LANE_RIGHT or expr == EXPR_BLINK_RIGHT:
      side = 1
    else:
      return

    # 깜빡이: 해당 쪽 눈이 감길 때 화살표도 함께 사라진다 (눈 윙크와 같은 리듬)
    if expr in (EXPR_BLINK_LEFT, EXPR_BLINK_RIGHT):
      if int(t * 2.0) % 2 == 0:
        return

    # 차선변경: 화살표가 바깥쪽으로 살짝 밀려났다 돌아옴
    slide = 0.0
    if expr in (EXPR_LANE_LEFT, EXPR_LANE_RIGHT):
      slide = 18.0 * s * (0.5 + 0.5 * math.sin(t * math.pi * 2.0 / 0.9))

    # 눈 바깥쪽 위치 (눈·테두리 빛띠와 겹치지 않게 간격 확보)
    eye_w = 340.0 * s
    eye_dx = 280.0 * s
    eye_y = -60.0 * s
    gap = 70.0 * s
    hw = 55.0 * s  # 화살표 반너비
    hh = 70.0 * s  # 화살표 반높이
    thick = max(10.0, 18.0 * s)
    _, _, rim_rgb = EYE_COLORS.get(expr, EYE_COLORS[EXPR_DRIVING])
    color = _col(rim_rgb)

    ex = cx + side * eye_dx
    ey = cy + eye_y
    ccx = ex + side * (eye_w / 2.0 + gap + hw + slide)
    # < : 꼭짓점이 왼쪽, > : 꼭짓점이 오른쪽
    apex_x = ccx + side * hw
    base_x = ccx - side * hw
    rl.draw_line_ex(rl.Vector2(apex_x, ey), rl.Vector2(base_x, ey - hh), thick, color)
    rl.draw_line_ex(rl.Vector2(apex_x, ey), rl.Vector2(base_x, ey + hh), thick, color)

  def _draw_zzz(self, cx: float, cy: float, s: float, t: float) -> None:
    """수면: 오른쪽 눈 위에서 "Zzz"가 천천히 떠올랐다 사라짐."""
    font_bold, _ = self._fonts()
    base_x = cx + 200.0 * s
    base_y = cy - 330.0 * s
    for i, (ch, size, phase) in enumerate((("Z", 110, 0.0), ("z", 80, 0.35), ("z", 60, 0.7))):
      cyc = (t * 0.25 + phase) % 1.0
      y = base_y - cyc * 140.0 * s
      alpha = int(230 * (1.0 - cyc))
      x = base_x + i * 55.0 * s
      rl.draw_text_ex(font_bold, ch, rl.Vector2(x, y), int(size * s), 0,
                      rl.Color(150, 200, 255, max(0, alpha)))

  def _draw_mini_hud(self, cx: float, y: float, s: float, accent: rl.Color) -> None:
    """인게이지 미니 HUD: 핸들 아이콘 + 현재 속도 + 목표 속도."""
    font_bold, font_medium = self._fonts()

    # 핸들 아이콘 (원 + 스포크 3개)
    icon_cx = cx - 320.0 * s
    icon_r = 34.0 * s
    rl.draw_ring(rl.Vector2(icon_cx, y), icon_r - 6.0 * s, icon_r, 0.0, 360.0, 32, accent)
    for ang in (math.pi / 2.0, 7.0 * math.pi / 6.0, 11.0 * math.pi / 6.0):
      rl.draw_line_ex(rl.Vector2(icon_cx, y),
                      rl.Vector2(icon_cx + (icon_r - 4.0 * s) * math.cos(ang),
                                 y + (icon_r - 4.0 * s) * math.sin(ang)),
                      max(4.0, 6.0 * s), accent)

    # 현재 속도 (km/h)
    speed_text = f"{int(round(self._v_ego * 3.6))}"
    speed_size = int(110 * s)
    speed_w = measure_text_cached(font_bold, speed_text, speed_size).x
    rl.draw_text_ex(font_bold, speed_text,
                    rl.Vector2(cx - speed_w / 2.0, y - speed_size / 2.0),
                    speed_size, 0, rl.WHITE)
    unit = "km/h"
    unit_size = int(44 * s)
    rl.draw_text_ex(font_medium, unit,
                    rl.Vector2(cx + speed_w / 2.0 + 16.0 * s, y - unit_size / 2.0 + 18.0 * s),
                    unit_size, 0, rl.Color(200, 200, 200, 255))

    # 목표 속도 (크루즈 설정 속도가 있을 때만)
    if self._v_target > 0.5:
      target_text = f"목표 {int(round(self._v_target * 3.6))}"
      target_size = int(64 * s)
      rl.draw_text_ex(font_medium, target_text,
                      rl.Vector2(cx + speed_w / 2.0 + 150.0 * s, y - target_size / 2.0),
                      target_size, 0, accent)
