# tests/lib.sh — minimal assertion helpers (bash 3.2)
_PASS=0
_FAIL=0

assert_eq() { # expected actual message
  if [ "$1" = "$2" ]; then
    _PASS=$((_PASS+1))
  else
    _FAIL=$((_FAIL+1))
    echo "FAIL: $3"
    echo "  expected: [$1]"
    echo "  actual:   [$2]"
  fi
}

assert_contains() { # haystack needle message
  case "$1" in
    *"$2"*) _PASS=$((_PASS+1)) ;;
    *) _FAIL=$((_FAIL+1)); echo "FAIL: $3"; echo "  [$1] does not contain [$2]" ;;
  esac
}

finish_tests() {
  echo "-- $_PASS passed, $_FAIL failed --"
  [ "$_FAIL" -eq 0 ]
}
