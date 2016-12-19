import sublime
import sublime_plugin
from os import path
import re
from collections import namedtuple


def get_syntax_test_tokens(view):
    """Parse the first line of the given view, to get a tuple,
    which will contain the start token for a syntax test, and the closing token too if present.
    If the file doesn't contain syntax tests, both elements of the tuple will be None."""

    line = view.line(0)
    match = None
    if line.size() < 1000:  # no point checking longer lines as they are unlikely to match
        first_line = view.substr(line)
        match = re.match(r'^(\S+)\s+SYNTAX TEST\s+"[^"]+"\s*(\S+)?$', first_line)
    if match is None:
        return (None, None)
    else:
        return (match.group(1), match.group(2))


def is_syntax_test_file(view):
    """Determine if the given view is a syntax test file or not.
    If the file has a name, check whether it begins with 'syntax_test_'.
    If it doesn't have a name / hasn't been saved yet, check the first line of the file."""

    name = view.file_name()
    if name is not None:
        name = path.basename(name)
        return name.startswith('syntax_test_')
    else:
        return get_syntax_test_tokens(view)[0] is not None


def get_details_of_test_assertion_line(view, pos):
    """Given a view and a character position, find:
    - the region of the line (3rd item in tuple aka `line_region`)
    - the comment marker (1st item in tuple aka `comment_marker_match`)
    - the assertion characters (2nd item in tuple aka `assertion_colrange`)
    """

    AssertionLineDetails = namedtuple(
        'AssertionLineDetails', ['comment_marker_match', 'assertion_colrange', 'line_region']
    )

    if not is_syntax_test_file(view):
        return AssertionLineDetails(None, None, None)
    tokens = get_syntax_test_tokens(view)
    if tokens[0] is None:
        return AssertionLineDetails(None, None, None)
    line_region = view.line(pos)
    line_text = view.substr(line_region)
    starts_with_comment_token = re.match(r'^\s*(' + re.escape(tokens[0]) + r')', line_text)
    assertion_colrange = None
    if starts_with_comment_token:
        assertion = re.match(r'\s*(?:(<-)|(\^+))', line_text[starts_with_comment_token.end():])
        if assertion:
            if assertion.group(1):
                assertion_colrange = (starts_with_comment_token.start(1),
                                      starts_with_comment_token.start(1))
            elif assertion.group(2):
                assertion_colrange = (starts_with_comment_token.end() + assertion.start(2),
                                      starts_with_comment_token.end() + assertion.end(2))

    return AssertionLineDetails(starts_with_comment_token, assertion_colrange, line_region)


def is_syntax_test_line(view, pos, must_contain_assertion):
    """Determine whether the line at the given character position is a syntax test line.
    It can optionally treat lines with comment markers but no assertion as a syntax test,
    useful for while the line is being written.
    """

    details = get_details_of_test_assertion_line(view, pos)
    if details.comment_marker_match:
        return not must_contain_assertion or details.assertion_colrange is not None
    return False


def get_details_of_line_being_tested(view):
    """Given a view, and starting from the cursor position, work upwards
    to find all syntax test lines that occur before the line that is being tested.
    Return a tuple containing a list of assertion line details,
    along with the region of the line being tested."""

    if not is_syntax_test_file(view):
        return (None, None)

    lines = []
    pos = view.sel()[0].begin()
    first_line = True
    while pos >= 0:
        details = get_details_of_test_assertion_line(view, pos)
        pos = details.line_region.begin() - 1
        if details.assertion_colrange:
            lines.append(details)
        elif not first_line or not details.comment_marker_match:
            break
        elif details.comment_marker_match:
            lines.append(details)
        first_line = False

    return (lines, details.line_region)


class AlignSyntaxTest(sublime_plugin.TextCommand):
    """Insert enough spaces so that the cursor will be immediately to the right of the
    previous line's last syntax test assertion."""

    def run(self, edit):
        cursor = self.view.sel()[0]
        details = get_details_of_test_assertion_line(self.view, cursor.begin())
        if not details.comment_marker_match:
            return

        # find the last test assertion column on the previous line
        details = get_details_of_test_assertion_line(self.view, details.line_region.begin() - 1)
        if details.line_region is not None:
            self.view.insert(edit, cursor.end(), ' ' * (
                details.assertion_colrange[1] - self.view.rowcol(cursor.begin())[1]
            ))
        self.view.run_command('suggest_syntax_test')


class SuggestSyntaxTest(sublime_plugin.TextCommand):
    """Intelligently suggest where the syntax test assertions should be placed,
    based on the scopes on the line being tested, and where they change."""

    def run(self, edit, character='^'):
        """Available parameters:
        edit (sublime.Edit)
            The edit parameter from TextCommand.
        character (str) = '^'
            The character to insert when suggesting where the test assertions should go.
        """

        view = self.view
        view.replace(edit, view.sel()[0], character)
        insert_at = view.sel()[0].begin()
        if not is_syntax_test_file(view):
            return

        lines, line = get_details_of_line_being_tested(view)
        end_token = get_syntax_test_tokens(view)[1]
        # don't duplicate the end token if it is on the line but not selected
        if end_token is not None and view.sel()[0].end() == lines[0].line_region.end():
            end_token = ' ' + end_token
        else:
            end_token = ''

        scopes = []
        length = 0
        # find the following columns on the line to be tested where the scopes don't change
        test_at_start_of_comment = False
        col = view.rowcol(insert_at)[1]
        assertion_colrange = lines[0].assertion_colrange or (-1, -1)
        if assertion_colrange[0] == assertion_colrange[1]:
            col = assertion_colrange[1]
            test_at_start_of_comment = True
            lines = lines[1:]

        for pos in range(line.begin() + col, line.end() + 1):
            scope = view.scope_name(pos)
            if len(scopes) > 0:
                if scope != scopes[0]:
                    break
            else:
                scopes.append(scope)
            length += 1
            if test_at_start_of_comment:
                break

        # find the unique scopes at each existing assertion position
        if lines and not test_at_start_of_comment:
            col_start, col_end = lines[0].assertion_colrange
            for pos in range(line.begin() + col_start, line.begin() + col_end):
                scope = view.scope_name(pos)
                if scope not in scopes:
                    scopes.append(scope)

        # find the shared scopes
        # TODO: more clever matching of partial scopes
        # i.e. meta.function.python, meta.function.parameters.python == meta.function
        shared_scopes = []
        for check_scope in scopes[0].split():
            if all(sublime.score_selector(scope, check_scope) > 0 for scope in scopes):
                shared_scopes.append(check_scope)

        # skip the first scope if it is the base scope
        # (crude check by scanning the first scope at the beginning of the file)
        start_index = 0
        if shared_scopes[0] == view.scope_name(0).split()[0]:
            start_index = 1

        scope = ' '.join(shared_scopes[start_index:])

        # delete the existing selection
        if not view.sel()[0].empty():
            view.erase(edit, view.sel()[0])

        view.insert(edit, insert_at, (character * length) + ' ' + scope + end_token)

        # move the selection to cover the added scope name,
        # so that the user can easily insert another ^ to extend the test
        view.sel().clear()
        view.sel().add(sublime.Region(
            insert_at + length,
            insert_at + length + len(' ' + scope + end_token)
        ))


class HighlightTestViewEventListener(sublime_plugin.ViewEventListener):
    def on_selection_modified_async(self):
        """When the selection changes, (re)move the highlight that shows where the current line's
        test assertions relate to."""

        cursor = self.view.sel()[0]
        highlight_only_cursor = False
        if cursor.empty():
            cursor = sublime.Region(cursor.begin(), cursor.end() + 1)
        else:
            highlight_only_cursor = re.match(r'^\^+$', self.view.substr(cursor)) is not None

        lines, line = get_details_of_line_being_tested(self.view)

        if not lines or not lines[0].assertion_colrange:
            self.view.erase_regions('current_syntax_test')
            return

        col_start, col_end = lines[0].assertion_colrange
        if highlight_only_cursor:
            col_start = self.view.rowcol(cursor.begin())[1]
            col_end = self.view.rowcol(cursor.end())[1]
        elif col_end == col_start:
            col_end += 1

        region = sublime.Region(line.begin() + col_start, line.begin() + col_end)

        prefs = sublime.load_settings('PackageDev.sublime-settings')
        scope = prefs.get('scope_for_syntax_test_highlight', 'text')
        styles = prefs.get('styles_for_syntax_test_highlight', ['DRAW_NO_FILL'])
        style_flags = 0
        # # the following available add_region styles are taken from the API documentation:
        # # http://www.sublimetext.com/docs/3/api_reference.html#sublime.View
        # # unfortunately, the `sublime` module doesn't encapsulate them for easy reference
        # for style in styles:
        #     if style in [
        #         'DRAW_EMPTY', 'HIDE_ON_MINIMAP', 'DRAW_EMPTY_AS_OVERWRITE', 'DRAW_NO_FILL',
        #         'DRAW_NO_OUTLINE', 'DRAW_SOLID_UNDERLINE', 'DRAW_STIPPLED_UNDERLINE',
        #         'DRAW_SQUIGGLY_UNDERLINE', 'HIDDEN', 'PERSISTENT'
        #     ]:
        #         style_flags |= getattr(sublime, style)
        for style in styles:
            # check the style matches the casing and naming used for constants in `sublime.py`
            if re.match(r'[A-Z]+[A-Z_]+', style):
                value = getattr(sublime, style)
                # ensure the value is an integer
                if isinstance(value, int):
                    style_flags |= value

        self.view.add_regions('current_syntax_test', [region], scope, '', style_flags)

    def on_query_context(self, key, operator, operand, match_all):
        """Respond to relevant syntax test keybinding contexts"""

        view = self.view
        # all contexts supported will have boolean results, so ignore regex operators
        if operator not in (sublime.OP_EQUAL, sublime.OP_NOT_EQUAL):
            return None

        def line_above_is_a_syntax_test():
            details = get_details_of_test_assertion_line(view, view.sel()[0].begin())
            if details.comment_marker_match is None:
                return False  # the current line doesn't start with a comment token
            else:
                return is_syntax_test_line(view, details.line_region.begin() - 1, True)

        keys = {
            "line_above_is_a_syntax_test": line_above_is_a_syntax_test,
            "current_line_is_a_syntax_test":
                lambda: is_syntax_test_line(view, view.sel()[0].begin(), False),
            "file_contains_syntax_tests":
                lambda: is_syntax_test_file(view)
        }

        if key not in keys:
            return None
        else:
            result = keys[key]() == bool(operand)
            if operator == sublime.OP_NOT_EQUAL:
                result = not result
            return result