"""
Custom managers for working with trees of objects.
"""
from django.db import connection, models
from django.utils.translation import ugettext as _

from mptt.exceptions import InvalidMove

__all__ = ['TreeManager']

qn = connection.ops.quote_name

class TreeManager(models.Manager):
    """
    A manager for working with trees of objects.
    """
    def __init__(self, parent_attr, left_attr, right_attr, tree_id_attr,
                 level_attr):
        """
        Tree related attributes for the model being managed are held as
        attributes of this manager for later use.
        """
        super(TreeManager, self).__init__()
        self.parent_attr = parent_attr
        self.left_attr = left_attr
        self.right_attr = right_attr
        self.tree_id_attr = tree_id_attr
        self.level_attr = level_attr

    def get_query_set(self):
        """
        Returns a ``QuerySet`` which contains all tree items, ordered in
        such a way that that trees appear in the order they were created
        and items appear in depth-first order within their tree.
        """
        return super(TreeManager, self).get_query_set().order_by(
            self.tree_id_attr, self.left_attr)

    def move_node(self, node, target, position='last-child'):
        """
        Moves ``node`` based on ``target``, relative to ``position``
        when appropriate, taking care of calling the appropriate manager
        method to do so.

        A ``target`` of ``None`` indicates that ``node`` should be
        turned into a root node.

        Valid values for ``position`` are ``'first-child'``,
        ``'last-child'``, ``'left'`` or ``'right'``.

        The given ``node`` will be modified to reflect its new tree
        state in the database.
        """
        if target is None:
            if node.is_child_node():
                self._make_child_root_node(node)
        elif target.is_root_node() and position in ['left', 'right']:
            self._make_sibling_of_root(node, target, position)
        else:
            if node.is_root_node():
                self._move_root_node(node, target, position)
            else:
                self._move_child_node(node, target, position)

    def _calculate_inter_tree_move_values(self, node, target, position):
        """
        Calculates values required when moving ``node`` into the
        ``target``, positioning it relative to ``target`` as specified
        by ``position``.
        """
        left = getattr(node, self.left_attr)
        level = getattr(node, self.level_attr)
        target_left = getattr(target, self.left_attr)
        target_right = getattr(target, self.right_attr)
        target_level = getattr(target, self.level_attr)

        if position == 'last-child' or position == 'first-child':
            if position == 'last-child':
                space_target = target_right - 1
            else:
                space_target = target_left
            level_change = level - target_level - 1
            parent = target
        elif position == 'left' or position == 'right':
            if position == 'left':
                space_target = target_left - 1
            else:
                space_target = target_right
            level_change = level - target_level
            parent = getattr(target, self.parent_attr)
        else:
            raise ValueError(_('An invalid position was given: %s.') % position)

        left_right_change = left - space_target - 1
        return space_target, level_change, left_right_change, parent

    def _close_gap(self, size, target, tree_id):
        """
        Closes a gap of a certain size after the given target point in
        the tree with the given id.
        """
        self._manage_space(size, target, tree_id, '-')

    def _create_space(self, size, target, tree_id):
        """
        Creates a space of a certain size after the given target point
        in the tree with the given id.
        """
        self._manage_space(size, target, tree_id, '+')

    def _get_next_tree_id(self):
        """
        Determines the next available tree id for the tree managed by
        this manager.
        """
        opts = self.model._meta
        cursor = connection.cursor()
        cursor.execute('SELECT MAX(%s) FROM %s' % (
            qn(opts.get_field(self.tree_id_attr).column),
            qn(opts.db_table)))
        row = cursor.fetchone()
        return row[0] and (row[0] + 1) or 1

    def _inter_tree_move_and_close_gap(self, node, level_change,
            left_right_change, new_tree_id, parent_pk=None):
        """
        Handles moving a subtree which is headed by ``node`` from its
        current tree to another tree, with the given set of changes
        being applied to ``node`` and its descendants, closing the gap
        left by moving the tree as it does so.

        If ``parent_pk`` is ``None``, this indicates that ``node`` is
        being moved to a brand new tree as its root node, and will thus
        have its parent field set to ``NULL``. Otherwise, ``node`` will
        have ``parent_pk`` set for its parent field.
        """
        opts = self.model._meta
        inter_tree_move_query = """
        UPDATE %(table)s
        SET %(level)s = CASE
                WHEN %(left)s >= %%s AND %(left)s <= %%s
                    THEN %(level)s - %%s
                ELSE %(level)s END,
            %(tree_id)s = CASE
                WHEN %(left)s >= %%s AND %(left)s <= %%s
                    THEN %%s
                ELSE %(tree_id)s END,
            %(left)s = CASE
                WHEN %(left)s >= %%s AND %(left)s <= %%s
                    THEN %(left)s - %%s
                WHEN %(left)s > %%s
                    THEN %(left)s - %%s
                ELSE %(left)s END,
            %(right)s = CASE
                WHEN %(right)s >= %%s AND %(right)s <= %%s
                    THEN %(right)s - %%s
                WHEN %(right)s > %%s
                    THEN %(right)s - %%s
                ELSE %(right)s END,
            %(parent)s = CASE
                WHEN %(pk)s = %%s
                    THEN %(new_parent)s
                ELSE %(parent)s END
        WHERE %(tree_id)s = %%s""" % {
            'table': qn(opts.db_table),
            'level': qn(opts.get_field(self.level_attr).column),
            'left': qn(opts.get_field(self.left_attr).column),
            'tree_id': qn(opts.get_field(self.tree_id_attr).column),
            'right': qn(opts.get_field(self.right_attr).column),
            'parent': qn(opts.get_field(self.parent_attr).column),
            'pk': qn(opts.pk.column),
            'new_parent': parent_pk is None and 'NULL' or '%s',
        }

        left = getattr(node, self.left_attr)
        right = getattr(node, self.right_attr)
        gap_size = right - left + 1
        gap_target_left = left - 1
        params = [
            left, right, level_change,
            left, right, new_tree_id,
            left, right, left_right_change,
            gap_target_left, gap_size,
            left, right, left_right_change,
            gap_target_left, gap_size,
            node.pk,
            getattr(node, self.tree_id_attr)
        ]
        if parent_pk is not None:
            params.insert(-1, parent_pk)
        cursor = connection.cursor()
        cursor.execute(inter_tree_move_query, params)

    def _make_child_root_node(self, node, new_tree_id=None):
        """
        Removes ``node`` from its tree, making it the root node of a new
        tree.

        The given ``node`` will be modified to reflect its new tree
        state in the database.

        If ``new_tree_id`` is not specified a new tree id will be
        generated.
        """
        left = getattr(node, self.left_attr)
        right = getattr(node, self.right_attr)
        level = getattr(node, self.level_attr)
        tree_id = getattr(node, self.tree_id_attr)
        if not new_tree_id:
            new_tree_id = self._get_next_tree_id()
        left_right_change = left - 1

        self._inter_tree_move_and_close_gap(node, level, left_right_change,
                                            new_tree_id)

        # Update the node to be consistent with the updated
        # tree in the database.
        setattr(node, self.left_attr, left - left_right_change)
        setattr(node, self.right_attr, right - left_right_change)
        setattr(node, self.level_attr, 0)
        setattr(node, self.tree_id_attr, new_tree_id)
        setattr(node, self.parent_attr, None)

    def _make_sibling_of_root(self, node, target_root, position):
        """
        Since we use tree ids to reduce the number of rows affected by
        tree mangement during insertion and deletion, root nodes are not
        true siblings, so making an item a sibling of a root node is a
        special case.
        """
        if node == target_root:
            raise InvalidMove(_('A node may not be made a sibling of itself.'))

        opts = self.model._meta
        space_query = """
        UPDATE %(table)s
        SET %(tree_id)s = %(tree_id)s + 1
        WHERE %(tree_id)s > %%s
        """ % {
            'table': qn(opts.db_table),
            'tree_id': qn(opts.get_field(self.tree_id_attr).column),
        }

        target_tree_id = getattr(target_root, self.tree_id_attr)
        if position == 'left':
            space_target = target_tree_id - 1
            new_tree_id = target_tree_id
        elif position == 'right':
            space_target = target_tree_id
            new_tree_id = target_tree_id + 1
        else:
            raise ValueError(_('An invalid position was given: %s.') % position)

        cursor = connection.cursor()
        cursor.execute(space_query, space_target)

        if node.is_child_node():
            self._make_child_root_node(node, new_tree_id)
        else:
            cursor.execute("""
            UPDATE %(table)s
            SET %(tree_id)s = %%s
            WHERE %(tree_id)s = %%s
            """ % {
                'table': qn(opts.db_table),
                'tree_id': qn(opts.get_field(self.tree_id_attr).column),
            }, [getattr(node, self.tree_id_attr), new_tree_id])
            setattr(node, self.tree_id_attr, new_tree_id)

    def _manage_space(self, size, target, tree_id, operator):
        """
        Manages spaces in a tree by changing the values of the left and
        right columns after a given target point.
        """
        opts = self.model._meta
        space_query = """
        UPDATE %(table)s
        SET %(left)s = CASE
                WHEN %(left)s > %%s
                    THEN %(left)s %(operator)s %%s
                ELSE %(left)s END,
            %(right)s = CASE
                WHEN %(right)s > %%s
                    THEN %(right)s %(operator)s %%s
                ELSE %(right)s END
        WHERE %(tree_id)s = %%s
          AND (%(left)s > %%s OR %(right)s > %%s)""" % {
            'table': qn(opts.db_table),
            'left': qn(opts.get_field(self.left_attr).column),
            'right': qn(opts.get_field(self.right_attr).column),
            'operator': operator,
            'tree_id': qn(opts.get_field(self.tree_id_attr).column),
        }
        cursor = connection.cursor()
        cursor.execute(space_query, [target, size, target, size, tree_id,
                                     target, target])

    def _move_child_node(self, node, target, position):
        """
        Takes care of calling the appropriate method to move a child
        ``node`` based on ``target``.
        """
        tree_id = getattr(node, self.tree_id_attr)
        target_tree_id = getattr(target, self.tree_id_attr)

        if (getattr(node, self.tree_id_attr) ==
            getattr(target, self.tree_id_attr)):
            self._move_child_within_tree(node, target, position)
        else:
            self._move_child_to_new_tree(node, target, position)

    def _move_child_to_new_tree(self, node, target, position):
        """
        Moves ``node`` to a different tree, inserting it relative to the
        given ``target`` node in the new tree as specified by
        ``position``.

        The given ``node`` will be modified to reflect its new tree
        state in the database.
        """
        left = getattr(node, self.left_attr)
        right = getattr(node, self.right_attr)
        level = getattr(node, self.level_attr)
        target_left = getattr(target, self.left_attr)
        target_right = getattr(target, self.right_attr)
        target_level = getattr(target, self.level_attr)
        tree_id = getattr(node, self.tree_id_attr)
        new_tree_id = getattr(target, self.tree_id_attr)

        space_target, level_change, left_right_change, parent = \
            self._calculate_inter_tree_move_values(node, target, position)

        tree_width = right - left + 1

        # Make space for the subtree which will be moved
        self._create_space(tree_width, space_target, new_tree_id)
        # Move the subtree
        self._inter_tree_move_and_close_gap(node, level_change,
            left_right_change, new_tree_id, parent.pk)

        # Update the node to be consistent with the updated
        # tree in the database.
        setattr(node, self.left_attr, left - left_right_change)
        setattr(node, self.right_attr, right - left_right_change)
        setattr(node, self.level_attr, level - level_change)
        setattr(node, self.tree_id_attr, new_tree_id)
        setattr(node, self.parent_attr, parent)

    def _move_child_within_tree(self, node, target, position):
        """
        Moves child node ``node`` within its current tree relative to the
        given ``target`` node as specified by ``position``.

        The given ``node`` will be modified to reflect its new tree
        state in the database.
        """
        left = getattr(node, self.left_attr)
        right = getattr(node, self.right_attr)
        level = getattr(node, self.level_attr)
        width = right - left + 1
        tree_id = getattr(node, self.tree_id_attr)
        target_left = getattr(target, self.left_attr)
        target_right = getattr(target, self.right_attr)
        target_level = getattr(target, self.level_attr)

        if position == 'last-child' or position == 'first-child':
            if node == target:
                raise InvalidMove(_('A node may not be made a child of itself.'))
            elif left < target_left < right:
                raise InvalidMove(_('A node may not be made a child of any of its descendants.'))
            if position == 'last-child':
                if target_right > right:
                    new_left = target_right - width
                    new_right = target_right - 1
                else:
                    new_left = target_right
                    new_right = target_right + width - 1
            else:
                if target_left > left:
                    new_left = target_left - width + 1
                    new_right = target_left
                else:
                    new_left = target_left + 1
                    new_right = target_left + width
            level_change = level - target_level - 1
            parent = target
        elif position == 'left' or position == 'right':
            if node == target:
                raise InvalidMove(_('A node may not be made a sibling of itself.'))
            elif left < target_left < right:
                raise InvalidMove(_('A node may not be made a sibling of any of its descendants.'))
            if position == 'left':
                if target_left > left:
                    new_left = target_left - width
                    new_right = target_left - 1
                else:
                    new_left = target_left
                    new_right = target_left + width - 1
            else:
                if target_right > right:
                    new_left = target_right - width + 1
                    new_right = target_right
                else:
                    new_left = target_right + 1
                    new_right = target_right + width
            level_change = level - target_level
            parent = getattr(target, self.parent_attr)
        else:
            raise ValueError(_('An invalid position was given: %s.') % position)

        left_boundary = min(left, new_left)
        right_boundary = max(right, new_right)
        left_right_change = new_left - left
        gap_size = width
        if left_right_change > 0:
            gap_size = -gap_size

        opts = self.model._meta
        # The level update must come before the left update to keep
        # MySQL happy - left seems to refer to the updated value
        # immediately after its update has been specified in the query
        # with MySQL, but not with SQLite or Postgres.
        move_subtree_query = """
        UPDATE %(table)s
        SET %(level)s = CASE
                WHEN %(left)s >= %%s AND %(left)s <= %%s
                  THEN %(level)s - %%s
                ELSE %(level)s END,
            %(left)s = CASE
                WHEN %(left)s >= %%s AND %(left)s <= %%s
                  THEN %(left)s + %%s
                WHEN %(left)s >= %%s AND %(left)s <= %%s
                  THEN %(left)s + %%s
                ELSE %(left)s END,
            %(right)s = CASE
                WHEN %(right)s >= %%s AND %(right)s <= %%s
                  THEN %(right)s + %%s
                WHEN %(right)s >= %%s AND %(right)s <= %%s
                  THEN %(right)s + %%s
                ELSE %(right)s END,
            %(parent)s = CASE
                WHEN %(pk)s = %%s
                  THEN %%s
                ELSE %(parent)s END
        WHERE %(tree_id)s = %%s""" % {
            'table': qn(opts.db_table),
            'level': qn(opts.get_field(self.level_attr).column),
            'left': qn(opts.get_field(self.left_attr).column),
            'right': qn(opts.get_field(self.right_attr).column),
            'parent': qn(opts.get_field(self.parent_attr).column),
            'pk': qn(opts.pk.column),
            'tree_id': qn(opts.get_field(self.tree_id_attr).column),
        }

        cursor = connection.cursor()
        cursor.execute(move_subtree_query, [
            left, right, level_change,
            left, right, left_right_change,
            left_boundary, right_boundary, gap_size,
            left, right, left_right_change,
            left_boundary, right_boundary, gap_size,
            node.pk, parent.pk,
            tree_id])

        # Update the node to be consistent with the updated
        # tree in the database.
        setattr(node, self.left_attr, new_left)
        setattr(node, self.right_attr, new_right)
        setattr(node, self.level_attr, level - level_change)
        setattr(node, self.parent_attr, parent)

    def _move_root_node(self, root, target, position):
        """
        Moves ``node`` (which must be a root node) to a different tree,
        inserting it relative to the given ``target`` node in the new
        tree as specified by ``position``.

        The given ``root`` will be modified to reflect its new tree
        state in the database.
        """
        left = getattr(root, self.left_attr)
        right = getattr(root, self.right_attr)
        level = getattr(root, self.level_attr)
        tree_id = getattr(root, self.tree_id_attr)
        new_tree_id = getattr(target, self.tree_id_attr)
        width = right - left + 1

        if root == target:
            raise InvalidMove(_('A node may not be made a child of itself.'))
        elif tree_id == new_tree_id:
            raise InvalidMove(_('A node may not be made a child of any of its descendants.'))

        space_target, level_change, left_right_change, parent = \
            self._calculate_inter_tree_move_values(root, target, position)

        # Create space for the tree which will be inserted
        self._create_space(width, space_target, new_tree_id)

        # Move the root node, making it a child node
        opts = self.model._meta
        move_tree_query = """
        UPDATE %(table)s
        SET %(level)s = %(level)s - %%s,
            %(left)s = %(left)s - %%s,
            %(right)s = %(right)s - %%s,
            %(tree_id)s = %%s,
            %(parent)s = CASE
                WHEN %(pk)s = %%s
                    THEN %%s
                ELSE %(parent)s END
        WHERE %(left)s >= %%s AND %(left)s <= %%s
          AND %(tree_id)s = %%s""" % {
            'table': qn(opts.db_table),
            'level': qn(opts.get_field(self.level_attr).column),
            'left': qn(opts.get_field(self.left_attr).column),
            'right': qn(opts.get_field(self.right_attr).column),
            'tree_id': qn(opts.get_field(self.tree_id_attr).column),
            'parent': qn(opts.get_field(self.parent_attr).column),
            'pk': qn(opts.pk.column),
        }
        cursor = connection.cursor()
        cursor.execute(move_tree_query, [level_change, left_right_change,
            left_right_change, new_tree_id, root.pk, parent.pk, left, right,
            tree_id])

        # Update the former root node to be consistent with the updated
        # tree in the database.
        setattr(root, self.left_attr, left - left_right_change)
        setattr(root, self.right_attr, right - left_right_change)
        setattr(root, self.level_attr, level - level_change)
        setattr(root, self.tree_id_attr, new_tree_id)
        setattr(root, self.parent_attr, parent)