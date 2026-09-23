"""Conservative cache dependency discovery, not a Dockerfile validator.

None means Docker must perform an ordinary build. In particular, shell text is
never searched for image flags, and heredoc contents are never instructions.
"""
from re import IGNORECASE, match
from shlex import shlex, split


def _heredocs(command):
    delimiters = []
    quote, index = None, 0
    while index < len(command):
        char = command[index]
        if char == '\\' and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif command[index:index + 2] == '<<':
            word = match(r'<<(-?)([^\s;&|<>]+)', command[index:])
            if word is None:
                raise ValueError('unresolved heredoc delimiter')
            delimiter, = split(word[2])
            delimiters.append((delimiter, bool(word[1])))
            index += len(word[0])
            continue
        index += 1
    return delimiters


def external_images(dockerfile):
    """Literal external FROM, COPY --from and leading RUN --mount inputs."""
    try:
        return _external_images(dockerfile)
    except ValueError:
        return None


def _external_images(dockerfile):
    stages, external = set(), []
    lines = iter(dockerfile.splitlines())
    for line in lines:
        line = line.strip()
        if line.startswith('#'):
            if match(r'#\s*escape\s*=', line, flags=IGNORECASE) and not line.endswith('\\'):
                return None
            continue
        while line.endswith('\\'):
            following = next(lines, None)
            if following is None:
                return None
            if following.lstrip().startswith('#'):
                continue
            line = line[:-1] + ' ' + following.strip()
        instruction = line.split(maxsplit=1)
        if len(instruction) < 2:
            continue
        operation, command = instruction[0].upper(), instruction[1]
        references = []
        if operation == 'FROM':
            words = split(command)
            if words and words[0].startswith('--platform='):
                words.pop(0)
            if len(words) not in (1, 3) or (len(words) == 3 and words[1].upper() != 'AS'):
                return None
            if words[0].isdigit():
                return None  # Leave numeric FROM interpretation to Docker.
            if words[0].lower() not in stages:
                references.append(words[0])
            if len(words) == 3:
                stages.add(words[2].lower())
        elif operation in ('RUN', 'COPY'):
            # Read only the Docker option prefix. Once shell/JSON/file arguments
            # begin, --from/--mount are ordinary data, even if unquoted.
            lexer = shlex(command, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ''
            while command[lexer.instream.tell():].lstrip().startswith('--'):
                word = lexer.get_token()
                if operation == 'COPY' and word.startswith('--from='):
                    references.append(word[7:])
                elif operation == 'RUN' and word.startswith('--mount='):
                    options = dict(part.split('=', 1) for part in word[8:].split(',') if '=' in part)
                    if options.get('type') in ('secret', 'ssh'):
                        return None
                    if 'from' in options:
                        references.append(options['from'])
                elif '=' not in word:
                    return None  # Unknown option arity: leave it to Docker.
            # Quoted redirection text and JSON commands do not open heredocs.
            # Nested shell substitutions require a shell parser; do not guess.
            if '<<' in command and ('$(' in command or '`' in command):
                return None
            if not command.startswith('['):
                for delimiter, strip_tabs in _heredocs(command):
                    for body in lines:
                        if (body.lstrip('\t') if strip_tabs else body) == delimiter:
                            break
                    else:
                        return None
        elif operation == 'ONBUILD' or (operation == 'ADD' and '://' in command):
            return None
        for reference in references:
            if '$' in reference:
                return None
            if (operation == 'FROM' or reference.lower() not in stages) and reference != 'scratch' and not reference.isdigit():
                external.append(reference)
    return tuple(dict.fromkeys(external))
