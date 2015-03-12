**This page is no longer maintained. Please see the [upgrade notes](http://django-mptt.github.com/django-mptt/upgrade.html) in the documentation**



## `HEAD` - Backwards incompatible changes since `0.3.1` release ##

### `mptt.register()` removed - replaced by abstract models ###

Old way to set up your models (0.3.1 and earlier):
```
import mptt
class MyModel(models.Model):
    ...
mptt.register(MyModel, order_insertion_by='name')
```

This has now been replaced with model inheritance, and the old keyword args are now specified on an inner MPTTMeta class:

```
from mptt.models import MPTTModel
class MyModel(MPTTModel):
    ...
    class MPTTMeta:
        order_insertion_by = 'name'
```

See the [models documentation](http://django-mptt.github.com/django-mptt/models.html#setting-up-a-django-model-for-mptt) for more info.

## `0.3.0`, `0.3.1` - Backwards incompatible changes since 0.2 ##

### order\_insertion\_by ###

**NOTE** This was undone in 0.3.1 - in 0.3.1 you can use either a tuple, a list or just a string.

The `order_insertion_by` argument to `mptt.register` must now be a list of field names.

Old format:
```
mptt.register(MyModel, order_insertion_by='name')
```

New format:
```
mptt.register(MyModel, order_insertion_by=['name'])
```