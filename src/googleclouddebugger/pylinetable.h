#ifndef DEVTOOLS_CDBG_DEBUGLETS_PYTHON_PYLINETABLE_H_
#define DEVTOOLS_CDBG_DEBUGLETS_PYTHON_PYLINETABLE_H_

/* Python Linetable helper methods.
 * They are not part of the cpython api.
 * See https://peps.python.org/pep-0626/#out-of-process-debuggers-and-profilers
 */

// Things are different in 3.11.
// See https://github.com/python/cpython/Objects/locations.md
#if PY_VERSION_HEX >= 0x030B0000

typedef enum _PyCodeLocationInfoKind {
    /* short forms are 0 to 9 */
    PY_CODE_LOCATION_INFO_SHORT0 = 0,
    /* one lineforms are 10 to 12 */
    PY_CODE_LOCATION_INFO_ONE_LINE0 = 10,
    PY_CODE_LOCATION_INFO_ONE_LINE1 = 11,
    PY_CODE_LOCATION_INFO_ONE_LINE2 = 12,

    PY_CODE_LOCATION_INFO_NO_COLUMNS = 13,
    PY_CODE_LOCATION_INFO_LONG = 14,
    PY_CODE_LOCATION_INFO_NONE = 15
} _PyCodeLocationInfoKind;

/** Out of process API for initializing the location table. */
extern void _PyLineTable_InitAddressRange(
    const char *linetable,
    Py_ssize_t length,   
    int firstlineno,
    PyCodeAddressRange *range);

/** API for traversing the line number table. */
extern int _PyLineTable_NextAddressRange(PyCodeAddressRange *range);


void _PyLineTable_InitAddressRange(const char *linetable, Py_ssize_t length, int firstlineno, PyCodeAddressRange *range) {
    range->opaque.lo_next = linetable;
    range->opaque.limit = range->opaque.lo_next + length;
    range->ar_start = -1;
    range->ar_end = 0;
    range->opaque.computed_line = firstlineno;
    range->ar_line = -1;
}

static int
scan_varint(const uint8_t *ptr)
{
    unsigned int read = *ptr++;
    unsigned int val = read & 63;
    unsigned int shift = 0;
    while (read & 64) {
        read = *ptr++;
        shift += 6;
        val |= (read & 63) << shift;
    }
    return val;
}

static int
scan_signed_varint(const uint8_t *ptr)
{
    unsigned int uval = scan_varint(ptr);
    if (uval & 1) {
        return -(int)(uval >> 1);
    }
    else {
        return uval >> 1;
    }
}

static int
get_line_delta(const uint8_t *ptr)
{
    int code = ((*ptr) >> 3) & 15;
    switch (code) {
        case PY_CODE_LOCATION_INFO_NONE:
            return 0;
        case PY_CODE_LOCATION_INFO_NO_COLUMNS:
        case PY_CODE_LOCATION_INFO_LONG:
            return scan_signed_varint(ptr+1);
        case PY_CODE_LOCATION_INFO_ONE_LINE0:
            return 0;
        case PY_CODE_LOCATION_INFO_ONE_LINE1:
            return 1;
        case PY_CODE_LOCATION_INFO_ONE_LINE2:
            return 2;
        default:
            /* Same line */
            return 0;
    }
}

static int
is_no_line_marker(uint8_t b)
{
    return (b >> 3) == 0x1f;
}


#define ASSERT_VALID_BOUNDS(bounds) \
    assert(bounds->opaque.lo_next <=  bounds->opaque.limit && \
        (bounds->ar_line == -1 || bounds->ar_line == bounds->opaque.computed_line) && \
        (bounds->opaque.lo_next == bounds->opaque.limit || \
        (*bounds->opaque.lo_next) & 128))

static int
next_code_delta(PyCodeAddressRange *bounds)
{
    assert((*bounds->opaque.lo_next) & 128);
    return (((*bounds->opaque.lo_next) & 7) + 1) * sizeof(_Py_CODEUNIT);
}

static void
advance(PyCodeAddressRange *bounds)
{
    ASSERT_VALID_BOUNDS(bounds);
    bounds->opaque.computed_line += get_line_delta(reinterpret_cast<const uint8_t *>(bounds->opaque.lo_next));
    if (is_no_line_marker(*bounds->opaque.lo_next)) {
        bounds->ar_line = -1;
    }
    else {
        bounds->ar_line = bounds->opaque.computed_line;
    }
    bounds->ar_start = bounds->ar_end;
    bounds->ar_end += next_code_delta(bounds);
    do {
        bounds->opaque.lo_next++;
    } while (bounds->opaque.lo_next < bounds->opaque.limit &&
        ((*bounds->opaque.lo_next) & 128) == 0);
    ASSERT_VALID_BOUNDS(bounds);
}

static inline int
at_end(PyCodeAddressRange *bounds) {
    return bounds->opaque.lo_next >= bounds->opaque.limit;
}

int
_PyLineTable_NextAddressRange(PyCodeAddressRange *range)
{
    if (at_end(range)) {
        return 0;
    }
    advance(range);
    assert(range->ar_end > range->ar_start);
    return 1;
}
#elif PY_VERSION_HEX >= 0x030A0000
void
_PyLineTable_InitAddressRange(const char *linetable, Py_ssize_t length, int firstlineno, PyCodeAddressRange *range)
{
    range->opaque.lo_next = linetable;
    range->opaque.limit = range->opaque.lo_next + length;
    range->ar_start = -1;
    range->ar_end = 0;
    range->opaque.computed_line = firstlineno;
    range->ar_line = -1;
}

static void
advance(PyCodeAddressRange *bounds)
{
    bounds->ar_start = bounds->ar_end;
    int delta = ((unsigned char *)bounds->opaque.lo_next)[0];
    bounds->ar_end += delta;
    int ldelta = ((signed char *)bounds->opaque.lo_next)[1];
    bounds->opaque.lo_next += 2;
    if (ldelta == -128) {
        bounds->ar_line = -1;
    }
    else {
        bounds->opaque.computed_line += ldelta;
        bounds->ar_line = bounds->opaque.computed_line;
    }
}

static inline int
at_end(PyCodeAddressRange *bounds) {
    return bounds->opaque.lo_next >= bounds->opaque.limit;
}

int
_PyLineTable_NextAddressRange(PyCodeAddressRange *range)
{
    if (at_end(range)) {
        return 0;
    }
    advance(range);
    while (range->ar_start == range->ar_end) {
        assert(!at_end(range));
        advance(range);
    }
    return 1;
}
#endif

#endif  // DEVTOOLS_CDBG_DEBUGLETS_PYTHON_PYLINETABLE_H_
